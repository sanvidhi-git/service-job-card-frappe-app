"""End-to-end tests for the Car Cupid billing engine (blueprint §19).

Run: bench --site <site> run-tests --app car_cupid_service
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from car_cupid_service.api import counter, job_card, pos
from car_cupid_service.services import billing_service, stock_service
from car_cupid_service.services.billing_service import BillingValidationError
from car_cupid_service.tests import utils


def _key(tag):
	return f"test-{tag}-{frappe.generate_hash(length=10)}"


def _close_open_sessions():
	for name in frappe.get_all(
		"Counter Session", filters={"docstatus": 0, "cashier": frappe.session.user}, pluck="name"
	):
		frappe.db.set_value("Counter Session", name, "docstatus", 2)


class TestCarCupidFlow(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		utils.setup_test_world()
		cls.cca, cls.ccd, cls.ccc = (c[0] for c in utils.COMPANIES)
		_close_open_sessions()
		cls.session = utils.open_session(1000)
		utils.add_stock("CC-SEATCOVER", cls.cca, 20)
		utils.add_stock("CC-MATS", cls.ccc, 20)
		frappe.db.commit()

	def make_job(self, lines):
		return job_card.create(
			{
				"customer": utils.CUSTOMER,
				"vehicle": utils.VEHICLE,
				"odometer": 25000,
				"fuel_level": "1/2",
				"customer_complaint": "Seat covers, ceramic coating and mats",
				"default_sales_person": utils.SALES_PERSON,
				"items": lines,
				"force_new": 1,
			}
		)["name"]

	def three_company_job(self):
		return self.make_job(
			[
				{"item_code": "CC-SEATCOVER", "qty": 1},
				{"item_code": "CC-CERAMIC", "qty": 1},
				{"item_code": "CC-LABOUR", "qty": 2},
				{"item_code": "CC-MATS", "qty": 1},
			]
		)

	def prepare_job_for_billing(self, name):
		stock_service.request_materials(name)
		for mir in frappe.get_all(
			"Material Issue Request", filters={"job_card": name}, fields=["name", "pending_qty"]
		):
			stock_service.record_issue(mir.name, mir.pending_qty)
		job_card.set_work_status(name, None, "Completed")
		return frappe.get_doc("Service Job Card", name)

	# ------------------------------------------------------------------ tests
	def test_01_company_routing_from_item_defaults(self):
		name = self.three_company_job()
		jc = frappe.get_doc("Service Job Card", name)
		companies = {r.item_code: r.company for r in jc.items}
		self.assertEqual(companies["CC-SEATCOVER"], self.cca)
		self.assertEqual(companies["CC-CERAMIC"], self.ccd)
		self.assertEqual(companies["CC-MATS"], self.ccc)
		self.assertEqual(jc.vehicle_number, "MH12AB1234")
		self.assertTrue(all(r.sales_person == utils.SALES_PERSON for r in jc.items))
		self.assertEqual(jc.status, "Open")

	def test_02_billing_blocked_until_issue_and_completion(self):
		name = self.three_company_job()
		with self.assertRaises(BillingValidationError):
			billing_service.bill_source("Service Job Card", name, _key("block"), [])

	def test_03_three_company_billing_with_split_payment(self):
		name = self.three_company_job()
		store = frappe.get_doc("Car Cupid Settings").companies[0]
		store_before = utils.bin_qty("CC-SEATCOVER", store.default_warehouse)
		jc = self.prepare_job_for_billing(name)
		self.assertEqual(jc.status, "Ready for Billing")
		self.assertEqual(utils.bin_qty("CC-SEATCOVER", store.default_warehouse), store_before - 1)
		self.assertEqual(utils.bin_qty("CC-SEATCOVER", store.service_bay_warehouse), 1)

		preview = pos.preview_bill("Service Job Card", name)
		self.assertEqual(len(preview["companies"]), 3)
		total = preview["grand_total"]
		payments = [
			{"mode_of_payment": "Cash", "amount": 5000},
			{"mode_of_payment": "UPI", "amount": total - 5000, "reference_no": "UTR123456"},
		]
		key = _key("three")
		result = job_card.checkout(name, payments, key)
		self.assertEqual(result["status"], "Submitted", result)
		invoices = [c["sales_invoice"] for c in result["companies"]]
		self.assertEqual(len(invoices), 3)
		self.assertEqual(
			{frappe.db.get_value("Sales Invoice", i, "company") for i in invoices},
			{self.cca, self.ccd, self.ccc},
		)
		for inv in invoices:
			self.assertEqual(flt(frappe.db.get_value("Sales Invoice", inv, "outstanding_amount")), 0, inv)
		posted = [a for a in result["allocations"] if a["status"] == "Posted"]
		self.assertAlmostEqual(sum(a["amount"] for a in posted), total, places=2)
		for a in posted:
			pe = frappe.get_doc("Payment Entry", a["payment_entry"])
			self.assertEqual(pe.company, frappe.db.get_value("Sales Invoice", a["sales_invoice"], "company"))
			self.assertEqual(pe.cc_counter_session, self.session)

		# sold exactly once: bay stock consumed by the invoice, store untouched by the sale
		self.assertEqual(utils.bin_qty("CC-SEATCOVER", store.service_bay_warehouse), 0)
		self.assertEqual(utils.bin_qty("CC-SEATCOVER", store.default_warehouse), store_before - 1)

		jc.reload()
		self.assertEqual(jc.status, "Billed")
		self.assertEqual(jc.billing_status, "Fully Billed")
		self.assertEqual(len([i for i in jc.invoices if i.status != "Cancelled"]), 3)

		# idempotency: same key returns the same batch, no new invoices
		again = job_card.checkout(name, payments, key)
		self.assertEqual(again["batch"], result["batch"])
		self.assertEqual(frappe.db.count("Sales Invoice", {"cc_source_name": name, "docstatus": 1}), 3)
		# a new key finds nothing left to bill
		with self.assertRaises(BillingValidationError):
			job_card.checkout(name, payments, _key("again"))

		# incentives earned on every salesperson line
		ledger = frappe.get_all(
			"Service Incentive Ledger",
			filters={"sales_invoice": ("in", invoices)},
			fields=["incentive_amount", "eligible_net_sales"],
		)
		self.assertEqual(len(ledger), 4)
		for e in ledger:
			self.assertAlmostEqual(e.incentive_amount, flt(e.eligible_net_sales * 0.02, 2), places=2)

		# delivery
		job_card.mark_delivered(name)
		self.assertEqual(frappe.db.get_value("Service Job Card", name, "status"), "Delivered")

	def test_04_cancel_invoice_reopens_line_and_reverses_incentive(self):
		name = self.make_job([{"item_code": "CC-CERAMIC", "qty": 1}])
		job_card.set_work_status(name, None, "Completed")
		total = pos.preview_bill("Service Job Card", name)["grand_total"]
		result = job_card.checkout(name, [{"mode_of_payment": "Cash", "amount": total}], _key("cancel"))
		inv = result["companies"][0]["sales_invoice"]
		alloc = result["allocations"][0]
		pe = frappe.get_doc("Payment Entry", alloc["payment_entry"])
		pe.cancel()
		si = frappe.get_doc("Sales Invoice", inv)
		si.cancel()
		jc = frappe.get_doc("Service Job Card", name)
		self.assertEqual(jc.items[0].billed_qty, 0)
		self.assertEqual(jc.status, "Ready for Billing")
		net = sum(
			frappe.get_all(
				"Service Incentive Ledger", filters={"sales_invoice": inv}, pluck="incentive_amount"
			)
		)
		self.assertAlmostEqual(net, 0, places=2)
		batch = frappe.get_doc("Invoice Batch", result["batch"])
		self.assertEqual(batch.status, "Cancelled")

	def test_05_direct_pos_cash_memo_with_change(self):
		data = {
			"client_request_id": _key("memo"),
			"customer": utils.CUSTOMER,
			"vehicle": utils.VEHICLE,
			"items": [
				{"item_code": "CC-SEATCOVER", "qty": 1},
				{"item_code": "CC-MATS", "qty": 2},
			],
		}
		memo = pos.save_cash_memo(data)
		self.assertEqual(len(memo["company_summary"]), 2)
		# a retried save with the same client id must not create a second memo
		memo_again = pos.save_cash_memo(data)
		self.assertEqual(memo["name"], memo_again["name"])
		data["name"] = memo["name"]
		total = pos.preview_bill("Counter Cash Memo", memo["name"])["grand_total"]
		result = pos.checkout_cash_memo(
			data, [{"mode_of_payment": "Cash", "amount": total + 500}], _key("memo-pay")
		)
		self.assertEqual(result["status"], "Submitted", result)
		self.assertAlmostEqual(result["change_due"], 500, places=2)
		self.assertEqual(len(result["companies"]), 2)
		memo_doc = frappe.get_doc("Counter Cash Memo", memo["name"])
		self.assertEqual(memo_doc.status, "Billed")
		self.assertAlmostEqual(memo_doc.change_returned, 500, places=2)
		self.assertAlmostEqual(sum(a["amount"] for a in result["allocations"]), total, places=2)

	def test_06_partial_failure_and_safe_retry(self):
		"""Company C has no stock: A's invoice must survive, C fails, retry after receipt succeeds."""
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 0)
		for code, wh in (("CC-MATS", "Stores - CCC"),):
			qty = utils.bin_qty(code, wh)
			if qty:
				se = frappe.get_doc(
					{
						"doctype": "Stock Entry",
						"stock_entry_type": "Material Issue",
						"purpose": "Material Issue",
						"company": self.ccc,
						"items": [{"item_code": code, "qty": qty, "s_warehouse": wh}],
					}
				)
				se.insert()
				se.submit()
		data = {
			"client_request_id": _key("fail"),
			"customer": utils.CUSTOMER,
			"items": [{"item_code": "CC-SEATCOVER", "qty": 1}, {"item_code": "CC-MATS", "qty": 1}],
		}
		memo = pos.save_cash_memo(data)
		data["name"] = memo["name"]
		total = pos.preview_bill("Counter Cash Memo", memo["name"])["grand_total"]
		result = pos.checkout_cash_memo(
			data, [{"mode_of_payment": "Cash", "amount": total}], _key("fail-pay")
		)
		self.assertEqual(result["status"], "Partially Submitted", result)
		failed = [c for c in result["companies"] if c["status"] == "Failed"]
		ok = [c for c in result["companies"] if c["status"] == "Submitted"]
		self.assertEqual(len(failed), 1)
		self.assertEqual(failed[0]["company"], self.ccc)
		self.assertTrue(failed[0]["error"])
		self.assertEqual(len(ok), 1)
		self.assertEqual(frappe.db.get_value("Counter Cash Memo", memo["name"], "status"), "Partially Billed")

		utils.add_stock("CC-MATS", self.ccc, 5)
		retried = pos.retry_batch(result["batch"])
		self.assertEqual(retried["status"], "Submitted", retried)
		self.assertEqual(
			frappe.db.count("Sales Invoice", {"cc_invoice_batch": result["batch"], "docstatus": 1}), 2
		)
		self.assertEqual(frappe.db.get_value("Counter Cash Memo", memo["name"], "status"), "Billed")
		# the whole tendered amount is now allocated and nothing is outstanding
		self.assertAlmostEqual(retried["outstanding_amount"], 0, places=2)
		# retrying a finished batch does not create anything
		with self.assertRaises(frappe.ValidationError):
			pos.retry_batch(result["batch"])

	def test_07_payment_short_is_refused_before_invoicing(self):
		data = {
			"client_request_id": _key("short"),
			"customer": utils.CUSTOMER,
			"items": [{"item_code": "CC-CERAMIC", "qty": 1}],
		}
		memo = pos.save_cash_memo(data)
		data["name"] = memo["name"]
		before = frappe.db.count("Sales Invoice")
		with self.assertRaises(frappe.ValidationError):
			pos.checkout_cash_memo(data, [{"mode_of_payment": "Cash", "amount": 10}], _key("short-pay"))
		self.assertEqual(frappe.db.count("Sales Invoice"), before)
		with self.assertRaises(frappe.ValidationError):
			pos.checkout_cash_memo(data, [{"mode_of_payment": "UPI", "amount": 999999}], _key("upi-no-ref"))

	def test_08_material_return_and_quantity_guards(self):
		name = self.make_job([{"item_code": "CC-SEATCOVER", "qty": 2}])
		mirs = stock_service.request_materials(name)
		self.assertEqual(len(mirs), 1)
		with self.assertRaises(frappe.ValidationError):
			stock_service.record_issue(mirs[0], 3)  # over-issue
		stock_service.record_issue(mirs[0], 2)
		stock_service.record_return(mirs[0], 1)
		jc = frappe.get_doc("Service Job Card", name)
		self.assertEqual(jc.items[0].issued_qty, 2)
		self.assertEqual(jc.items[0].returned_qty, 1)
		self.assertEqual(jc.items[0].fulfilment_status, "Partially Issued")
		# cannot reduce qty below what is with the technician
		jc.items[0].qty = 0.5
		with self.assertRaises(frappe.ValidationError):
			jc.save()
		# partial billing is off: billing is blocked while 1 unit is still unissued
		with self.assertRaises(BillingValidationError):
			billing_service.bill_source("Service Job Card", name, _key("partial"), [])

	def test_09_discount_and_price_guards_for_counter_staff(self):
		user = "cc.counter@example.com"
		if not frappe.db.exists("User", user):
			u = frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "Counter", "send_welcome_email": 0}
			)
			u.insert(ignore_permissions=True)
			u.add_roles("Counter Salesperson")
		frappe.set_user(user)
		try:
			base = {
				"customer": utils.CUSTOMER,
				"items": [{"item_code": "CC-CERAMIC", "qty": 1, "discount_percentage": 25}],
			}
			with self.assertRaises(frappe.ValidationError):
				pos.save_cash_memo({**base, "client_request_id": _key("disc")})
			base["items"][0].update({"discount_percentage": 0, "price_list_rate": 1})
			with self.assertRaises(frappe.ValidationError):
				pos.save_cash_memo({**base, "client_request_id": _key("rate")})
			# within the limit is fine
			base["items"][0].update({"discount_percentage": 5, "price_list_rate": 0})
			memo = pos.save_cash_memo({**base, "client_request_id": _key("ok")})
			self.assertEqual(memo["items"][0]["rate"], 12000 * 0.95)
			# counter staff cannot bill
			with self.assertRaises(frappe.PermissionError):
				billing_service.bill_source("Counter Cash Memo", memo["name"], _key("perm"), [])
		finally:
			frappe.set_user("Administrator")

	def test_10_vehicle_number_rules(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "Customer Vehicle", "vehicle_number": "HELLO", "customer": utils.CUSTOMER}
			).insert()
		v = frappe.get_doc(
			{"doctype": "Customer Vehicle", "vehicle_number": "22 bh 1234 aa", "customer": utils.CUSTOMER}
		)
		if not frappe.db.exists("Customer Vehicle", "22BH1234AA"):
			v.insert()
		self.assertTrue(frappe.db.exists("Customer Vehicle", "22BH1234AA"))

	def test_99_counter_close_with_variance_needs_approval(self):
		s = counter.get_session(self.session)
		counter.add_movement(self.session, "Cash Out", 100, "Tea and snacks")
		s = counter.get_session(self.session)
		expected = s["expected_cash"]
		res = counter.close_session(self.session, physical_cash=expected - 500, variance_reason="Short")
		self.assertTrue(res.get("needs_approval"))
		self.assertEqual(frappe.db.get_value("Counter Session", self.session, "docstatus"), 0)
		res = counter.close_session(
			self.session, physical_cash=expected - 500, variance_reason="Short", approve_variance=1
		)
		self.assertEqual(res["status"], "Closed")
		self.assertAlmostEqual(res["variance"], -500, places=2)
