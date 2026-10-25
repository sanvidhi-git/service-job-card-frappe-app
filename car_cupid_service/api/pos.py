"""Endpoints used by the Car Cupid POS page."""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from car_cupid_service.services import billing_service, line_service
from car_cupid_service.services.payment_service import get_open_session
from car_cupid_service.services.settings import (
	denominations,
	get_company_map,
	get_default_company,
	get_settings,
	is_supervisor,
	max_discount,
)


@frappe.whitelist()
def get_boot() -> dict:
	settings = get_settings()
	roles = set(frappe.get_roles())
	mode_types = frappe.get_all(
		"Mode of Payment", filters={"enabled": 1}, fields=["name", "type"], order_by="name"
	)
	companies = [
		{
			"company": c.company,
			"label": c.short_label or c.company,
			"color": c.color or None,
			"has_bay": bool(c.service_bay_warehouse),
		}
		for c in get_company_map().values()
	]
	return {
		"user": frappe.session.user,
		"user_fullname": frappe.utils.get_fullname(),
		"companies": companies,
		"default_company": get_default_company(),
		"allow_company_override": cint(settings.allow_company_override) or is_supervisor(),
		"walk_in_customer": settings.walk_in_customer,
		"vehicle_required_for_pos": cint(settings.vehicle_required_for_pos),
		"material_issue_policy": settings.material_issue_policy,
		"allow_partial_billing": cint(settings.allow_partial_billing),
		"allow_credit_sale": cint(settings.allow_credit_sale),
		"max_discount": max_discount(),
		"cash_mode": settings.cash_mode_of_payment or "Cash",
		"modes": mode_types,
		"denominations": denominations(),
		"open_session": get_open_session(),
		"sales_person": line_service.get_user_sales_person(),
		"sales_persons": frappe.get_all(
			"Sales Person", filters={"enabled": 1, "is_group": 0}, pluck="name", limit=500
		),
		"technicians": frappe.get_all(
			"Employee",
			filters={"status": "Active"},
			fields=["name", "employee_name"],
			limit=500,
			order_by="employee_name",
		),
		"is_supervisor": is_supervisor(),
		"can": {
			"bill": bool(roles & billing_service.BILLING_ROLES),
			"store": bool(roles & {"Storekeeper", "Billing Supervisor", "System Manager", "Stock Manager"}),
			"job": bool(
				roles
				& {
					"Service Advisor",
					"Counter Salesperson",
					"Service Engineer",
					"Billing Supervisor",
					"System Manager",
					"Cashier",
				}
			),
			"counter": bool(roles & {"Cashier", "Billing Supervisor", "Accounts Manager", "System Manager"}),
		},
		"setup_ok": bool(companies),
	}


@frappe.whitelist()
def search_items(
	txt: str = "", company: str | None = None, customer: str | None = None, limit: int = 20
) -> list[dict]:
	"""Barcode first (exact), then item code / name. Each result carries company, price and stock."""
	frappe.has_permission("Item", "read", throw=True)
	txt = (txt or "").strip()
	limit = min(cint(limit) or 20, 40)
	codes: list[str] = []
	if txt:
		barcode_item = frappe.db.get_value("Item Barcode", {"barcode": txt}, "parent")
		if barcode_item:
			codes = [barcode_item]
		elif frappe.db.exists("Item", {"name": txt, "disabled": 0, "is_sales_item": 1}):
			codes = [txt]
	if not codes:
		like = f"%{txt}%"
		codes = frappe.get_list(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1, "has_variants": 0},
			or_filters=[
				["item_code", "like", like],
				["item_name", "like", like],
				["item_group", "like", like],
			]
			if txt
			else None,
			pluck="name",
			limit_page_length=limit,
			order_by="item_name asc",
		)
	out = []
	for code in codes:
		try:
			d = line_service.get_line_defaults(code, company, customer)
		except frappe.ValidationError:
			frappe.clear_messages()
			continue
		d["exact"] = len(codes) == 1 and bool(txt)
		out.append(d)
	return out


@frappe.whitelist()
def get_line(item_code: str, company: str | None = None, customer: str | None = None) -> dict:
	return line_service.get_line_defaults(item_code, company, customer)


@frappe.whitelist()
def get_stock(item_code: str, company: str) -> list[dict]:
	"""Stock of an item in the company's store and service-bay warehouses."""
	row = get_company_map().get(company)
	if not row:
		return []
	out = []
	for wh in filter(None, [row.default_warehouse, row.service_bay_warehouse]):
		out.append({"warehouse": wh, "actual_qty": line_service.get_stock_qty(item_code, wh)})
	return out


# ----------------------------------------------------------------------------- cash memo
def _apply_memo(doc, data: dict):
	doc.customer = data.get("customer")
	doc.vehicle = data.get("vehicle") or None
	doc.remarks = data.get("remarks")
	if data.get("counter_session"):
		doc.counter_session = data.get("counter_session")
	elif not doc.counter_session:
		doc.counter_session = get_open_session()
	existing = {r.name: r for r in doc.items}
	doc.set("items", [])
	for line in data.get("items") or []:
		row = {
			"item_code": line.get("item_code"),
			"company": line.get("company") or line_service.route_company(line.get("item_code")),
			"qty": flt(line.get("qty")) or 1,
			"uom": line.get("uom"),
			"price_list_rate": flt(line.get("price_list_rate")),
			"discount_percentage": flt(line.get("discount_percentage")),
			"sales_person": line.get("sales_person"),
			"item_type": line.get("item_type"),
			"warehouse": line.get("warehouse"),
		}
		prev = existing.get(line.get("name"))
		if prev:
			row["name"] = prev.name
			row["discount_approved_by"] = prev.discount_approved_by
		doc.append("items", row)
	apply_bill_discounts(doc, data.get("bill_discounts"))


def apply_bill_discounts(doc, discounts):
	"""Replace the pending discount lines with the ones sent; applied lines are always kept."""
	if discounts is None:
		return
	existing = {d.name: d for d in doc.get("bill_discounts") or []}
	keep = [d for d in existing.values() if d.status == "Applied"]
	doc.set("bill_discounts", [])
	for d in keep:
		doc.append("bill_discounts", d.as_dict())
	for d in discounts:
		prev = existing.get(d.get("name"))
		if prev and prev.status == "Applied":
			continue
		row = {
			"company": d.get("company"),
			"discount_amount": flt(d.get("discount_amount")),
			"reason": d.get("reason"),
		}
		if prev:
			row.update({"name": prev.name, "approved_by": prev.approved_by, "added_by": prev.added_by})
		doc.append("bill_discounts", row)


@frappe.whitelist()
def save_cash_memo(data: str | dict) -> dict:
	"""Create or update a draft memo. `client_request_id` makes a retried create return the same memo."""
	data = frappe.parse_json(data)
	name = data.get("name")
	if not name and data.get("client_request_id"):
		name = frappe.db.get_value(
			"Counter Cash Memo", {"client_request_id": data["client_request_id"]}, "name"
		)
	if name:
		doc = frappe.get_doc("Counter Cash Memo", name)
		doc.check_permission("write")
	else:
		doc = frappe.new_doc("Counter Cash Memo")
		doc.client_request_id = data.get("client_request_id")
		doc.cashier = frappe.session.user
	if doc.status != "Draft":
		frappe.throw(_("Memo {0} is already {1}").format(doc.name, doc.status))
	_apply_memo(doc, data)
	doc.save()
	return memo_payload(doc)


def memo_payload(doc) -> dict:
	d = doc.as_dict(no_default_fields=False)
	d["company_summary"] = line_service.company_summary(doc)
	return d


@frappe.whitelist()
def checkout_cash_memo(
	data: str | dict, payments: str | list, request_key: str, allow_credit: int = 0
) -> dict:
	data = frappe.parse_json(data)
	memo = save_cash_memo(data)
	result = billing_service.bill_source(
		"Counter Cash Memo", memo["name"], request_key, frappe.parse_json(payments), bool(cint(allow_credit))
	)
	result["memo"] = memo["name"]
	return result


@frappe.whitelist()
def preview_bill(source_doctype: str, source_name: str) -> dict:
	return billing_service.preview(source_doctype, source_name)


@frappe.whitelist()
def get_batch(batch: str) -> dict:
	frappe.has_permission("Invoice Batch", "read", batch, throw=True)
	return billing_service.batch_summary(batch)


@frappe.whitelist()
def retry_batch(batch: str) -> dict:
	return billing_service.retry_batch(batch)


@frappe.whitelist()
def recent_memos(limit: int = 20) -> list[dict]:
	return frappe.get_list(
		"Counter Cash Memo",
		fields=[
			"name",
			"customer_name",
			"vehicle_number",
			"status",
			"net_total",
			"grand_total",
			"invoice_batch",
			"modified",
		],
		filters={"modified": (">=", frappe.utils.add_days(nowdate(), -7))},
		order_by="modified desc",
		limit_page_length=min(cint(limit) or 20, 100),
	)


# ----------------------------------------------------------------------------- dashboard
@frappe.whitelist()
def dashboard() -> dict:
	today = getdate(nowdate())

	def count(doctype, filters):
		try:
			return frappe.db.count(doctype, filters) if frappe.has_permission(doctype, "read") else None
		except frappe.PermissionError:
			return None

	sales = []
	if frappe.has_permission("Sales Invoice", "read"):
		sales = frappe.db.sql(
			"""
			select company, count(*) as invoices, sum(grand_total) as amount
			from `tabSales Invoice`
			where docstatus = 1 and posting_date = %s and is_return = 0
				and ifnull(cc_source_doctype, '') != ''
			group by company
			""",
			today,
			as_dict=True,
		)
	session = get_open_session()
	session_doc = None
	if session:
		s = frappe.get_doc("Counter Session", session)
		s.compute_summary()
		session_doc = {
			"name": s.name,
			"counter_name": s.counter_name,
			"opening_time": s.opening_time,
			"opening_float": s.opening_float,
			"expected_cash": s.expected_cash,
			"cash_receipts": s.cash_receipts,
			"non_cash_receipts": s.non_cash_receipts,
		}
	return {
		"open_jobs": count("Service Job Card", {"status": ("in", ["Open", "Work In Progress"])}),
		"awaiting_issue": count(
			"Material Issue Request", {"status": ("in", ["Requested", "Partially Issued"])}
		),
		"ready_for_billing": count(
			"Service Job Card", {"status": ("in", ["Ready for Billing", "Partially Billed"])}
		),
		"billed_not_delivered": count("Service Job Card", {"status": "Billed"}),
		"overdue": count(
			"Service Job Card",
			{
				"status": ("not in", ["Billed", "Delivered", "Cancelled"]),
				"promised_delivery": ("<", frappe.utils.now()),
			},
		),
		"draft_memos": count("Counter Cash Memo", {"status": "Draft"}),
		"failed_batches": count("Invoice Batch", {"status": ("in", ["Failed", "Partially Submitted"])}),
		"sales_today": sales,
		"session": session_doc,
	}
