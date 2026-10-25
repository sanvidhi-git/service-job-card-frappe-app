"""Multi-company invoice engine shared by Service Job Cards and Counter Cash Memos.

Sequence (blueprint §8.1):
  1. Idempotency: a request key returns the existing batch instead of billing twice.
  2. Row lock on the source + refusal if another batch for it is in progress.
  3. Server-side eligibility (issue / work-completion gates, unbilled qty only).
  4. Build draft Sales Invoices per company; validate payment covers the computed totals
     *before* anything is submitted.
  5. Create an Invoice Batch and commit it (durable audit record).
  6. Submit each company invoice inside its own savepoint and commit it; one company's failure
     never rolls back another's submitted invoice.
  7. Allocate the received payment to the submitted invoices and post Payment Entries.
  8. Refresh the source document and finalise the batch status.
Retries only touch failed companies and unposted allocations.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, now_datetime, nowdate

from car_cupid_service.services import payment_service
from car_cupid_service.services.settings import get_company_row, get_settings

BILLING_ROLES = {"Cashier", "Billing Supervisor", "Accounts Manager", "System Manager"}
SOURCE_DOCTYPES = ("Service Job Card", "Counter Cash Memo")


class BillingValidationError(frappe.ValidationError):
	pass


# ----------------------------------------------------------------------------- eligibility
def eligible_lines(doc) -> tuple[list[dict], list[str]]:
	"""Return [(row, qty_to_bill, warehouse)] and a list of blocking messages."""
	settings = get_settings()
	partial = cint(settings.allow_partial_billing)
	issue_required = settings.material_issue_policy == "Issue Required for Stock Items"
	is_job = doc.doctype == "Service Job Card"
	out, blockers = [], []

	for row in doc.items:
		if getattr(row, "work_status", None) == "Cancelled":
			continue
		remaining = flt(row.qty) - flt(row.billed_qty)
		if remaining <= 0:
			continue
		qty = remaining
		warehouse = row.warehouse
		label = _("Row {0} {1}").format(row.idx, row.item_name or row.item_code)

		if is_job and cint(row.is_stock_item) and issue_required:
			company_row = get_company_row(row.company)
			available = flt(row.issued_qty) - flt(row.returned_qty) - flt(row.billed_qty)
			qty = min(remaining, max(available, 0))
			warehouse = company_row.service_bay_warehouse
			if qty < remaining:
				blockers.append(
					_("{0}: {1} of {2} still to be issued from stores").format(
						label, remaining - qty, remaining
					)
				)
		elif is_job and cint(row.is_stock_item) and settings.material_issue_policy == "Request Only":
			# requested but stock is still in the store warehouse; invoice deducts from there
			warehouse = row.warehouse
		elif is_job and not cint(row.is_stock_item) and cint(settings.require_work_completion):
			if row.work_status != "Completed":
				qty = 0
				blockers.append(_("{0}: work is not marked Completed").format(label))

		if qty > 0:
			out.append({"row": row, "qty": qty, "warehouse": warehouse if cint(row.is_stock_item) else None})

	if blockers and not partial:
		return [], blockers
	return out, blockers


# ----------------------------------------------------------------------------- invoice building
def _sales_team(lines: list[dict]) -> list[dict]:
	totals: dict[str, float] = {}
	for line in lines:
		sp = line["row"].sales_person
		if sp:
			totals[sp] = totals.get(sp, 0) + flt(line["row"].rate) * flt(line["qty"])
	grand = sum(totals.values())
	if not grand:
		return []
	team, running = [], 0.0
	items = list(totals.items())
	for i, (sp, amt) in enumerate(items):
		pct = round(amt * 100.0 / grand, 2) if i < len(items) - 1 else round(100.0 - running, 2)
		running += pct
		team.append({"sales_person": sp, "allocated_percentage": pct})
	return team


def build_invoice(source, company: str, lines: list[dict], batch_name: str | None = None):
	company_row = get_company_row(company)
	si = frappe.new_doc("Sales Invoice")
	si.company = company
	si.customer = source.customer
	si.posting_date = nowdate()
	si.set_posting_time = 1
	si.due_date = nowdate()
	si.ignore_pricing_rule = 1
	if company_row.selling_price_list:
		si.selling_price_list = company_row.selling_price_list
	if company_row.cost_center:
		si.cost_center = company_row.cost_center
	si.update_stock = 1 if any(line["warehouse"] for line in lines) else 0
	si.cc_invoice_batch = batch_name
	si.cc_source_doctype = source.doctype
	si.cc_source_name = source.name
	si.cc_vehicle = source.get("vehicle")
	si.cc_counter_session = source.get("counter_session")
	si.remarks = _("Car Cupid {0} {1}{2}").format(
		source.doctype, source.name, f" — {source.vehicle_number}" if source.get("vehicle_number") else ""
	)
	for line in lines:
		row = line["row"]
		item = {
			"item_code": row.item_code,
			"item_name": row.item_name,
			"qty": flt(line["qty"]),
			"uom": row.uom,
			"price_list_rate": flt(row.price_list_rate),
			"discount_percentage": flt(row.discount_percentage),
			"rate": flt(row.rate),
			"cc_sales_person": row.sales_person,
			"cc_item_type": row.item_type,
			"cc_source_row": row.name,
		}
		if line["warehouse"]:
			item["warehouse"] = line["warehouse"]
		if company_row.cost_center:
			item["cost_center"] = company_row.cost_center
		si.append("items", item)

	for member in _sales_team(lines):
		si.append("sales_team", member)

	si.set_missing_values()
	# set_missing_values may refetch prices; the counter price is authoritative
	by_row = {line["row"].name: line["row"] for line in lines}
	for item in si.items:
		row = by_row[item.cc_source_row]
		item.price_list_rate = flt(row.price_list_rate)
		item.discount_percentage = flt(row.discount_percentage)
		item.rate = flt(row.rate)

	_apply_taxes(si, company_row)
	si.calculate_taxes_and_totals()
	return si


def _apply_taxes(si, company_row):
	from erpnext.controllers.accounts_controller import get_default_taxes_and_charges, get_taxes_and_charges

	template = company_row.taxes_and_charges
	if template:
		si.taxes_and_charges = template
		si.set("taxes", [])
		for tax in get_taxes_and_charges("Sales Taxes and Charges Template", template) or []:
			si.append("taxes", tax)
	elif not si.get("taxes"):
		defaults = get_default_taxes_and_charges("Sales Taxes and Charges Template", company=si.company) or {}
		if defaults.get("taxes_and_charges"):
			si.taxes_and_charges = defaults["taxes_and_charges"]
			for tax in defaults.get("taxes") or []:
				si.append("taxes", tax)


def invoice_total(si) -> float:
	return (
		flt(si.rounded_total)
		if flt(si.rounded_total) and not cint(si.disable_rounded_total)
		else flt(si.grand_total)
	)


# ----------------------------------------------------------------------------- public entry points
def _check_billing_roles():
	if not BILLING_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("Only a Cashier or Billing Supervisor can generate invoices."), frappe.PermissionError)


def batch_summary(batch_name: str) -> dict:
	b = frappe.get_doc("Invoice Batch", batch_name)
	settings = get_settings()
	return {
		"batch": b.name,
		"status": b.status,
		"source_doctype": b.source_doctype,
		"source_name": b.source_name,
		"customer_name": b.customer_name,
		"total_amount": b.total_amount,
		"paid_amount": b.paid_amount,
		"outstanding_amount": b.outstanding_amount,
		"change_due": flt(b.change_due),
		"companies": [
			{
				"company": r.company,
				"label": (get_company_row(r.company, throw=False) or {}).get("short_label") or r.company,
				"status": r.status,
				"sales_invoice": r.sales_invoice,
				"grand_total": r.grand_total,
				"error": r.error,
			}
			for r in b.companies
		],
		"allocations": [
			{
				"company": a.company,
				"sales_invoice": a.sales_invoice,
				"mode_of_payment": a.mode_of_payment,
				"amount": a.amount,
				"status": a.status,
				"payment_entry": a.payment_entry,
				"error": a.error,
			}
			for a in b.allocations
		],
		"memo_print_format": "Car Cupid Consolidated Memo",
		"settings_partial": cint(settings.allow_partial_billing),
	}


def preview(source_doctype: str, source_name: str) -> dict:
	"""Company-wise totals (with tax) of what would be billed now, without creating anything."""
	if source_doctype not in SOURCE_DOCTYPES:
		frappe.throw(_("Invalid source"))
	source = frappe.get_doc(source_doctype, source_name)
	source.check_permission("read")
	lines, blockers = eligible_lines(source)
	by_company: dict[str, list] = {}
	for line in lines:
		by_company.setdefault(line["row"].company, []).append(line)
	companies = []
	for company, clines in by_company.items():
		si = build_invoice(source, company, clines)
		row = get_company_row(company)
		companies.append(
			{
				"company": company,
				"label": row.short_label or company,
				"color": row.color,
				"lines": len(clines),
				"net_total": flt(si.net_total),
				"tax": flt(si.total_taxes_and_charges),
				"grand_total": invoice_total(si),
			}
		)
	return {
		"companies": companies,
		"modes": payment_service.usable_modes([c["company"] for c in companies]),
		"grand_total": sum(c["grand_total"] for c in companies),
		"blockers": blockers,
		"can_bill": bool(companies),
	}


def bill_source(
	source_doctype: str,
	source_name: str,
	request_key: str,
	payments: list[dict] | None = None,
	allow_credit: bool = False,
) -> dict:
	_check_billing_roles()
	if source_doctype not in SOURCE_DOCTYPES:
		frappe.throw(_("Invalid source document type"))
	if not request_key or len(request_key) < 8:
		frappe.throw(_("A request key is required so a retried click cannot bill twice."))

	# 1. idempotency
	existing = frappe.db.get_value("Invoice Batch", {"request_key": request_key}, "name")
	if existing:
		return batch_summary(existing)

	# 2. lock + concurrency guard
	frappe.db.get_value(source_doctype, source_name, "name", for_update=True)
	running = frappe.db.get_value(
		"Invoice Batch",
		{
			"source_doctype": source_doctype,
			"source_name": source_name,
			"status": "In Progress",
			"modified": (">", add_to_date(now_datetime(), minutes=-15)),
		},
		"name",
	)
	if running:
		frappe.throw(
			_("Billing for {0} is already in progress (batch {1}). Please wait a moment and refresh.").format(
				source_name, running
			),
			title=_("Billing In Progress"),
		)

	source = frappe.get_doc(source_doctype, source_name)
	source.check_permission("write")
	if source.status == "Cancelled":
		frappe.throw(_("{0} is cancelled").format(source_name))
	if not source.customer:
		frappe.throw(_("Select a customer first"))

	# recompute quantities from the database before deciding what is billable
	from car_cupid_service.services.source_status import refresh_source

	source = refresh_source(source_doctype, source_name)

	# 3. eligibility
	lines, blockers = eligible_lines(source)
	if not lines:
		msg = "<br>".join(blockers) if blockers else _("Nothing is left to bill on {0}.").format(source_name)
		frappe.throw(msg, title=_("Not Ready for Billing"), exc=BillingValidationError)

	by_company: dict[str, list] = {}
	for line in lines:
		by_company.setdefault(line["row"].company, []).append(line)

	# 4. draft invoices + payment validation before anything is committed
	payments = payment_service.clean_payments(payments)
	drafts = {company: build_invoice(source, company, clines) for company, clines in by_company.items()}
	total = sum(invoice_total(si) for si in drafts.values())
	change_due = payment_service.validate_payment_cover(payments, total, allow_credit)
	payment_service.validate_mode_accounts(payments, list(drafts))
	session = payment_service.resolve_session(source, payments)

	# 5. durable batch record
	batch = frappe.get_doc(
		{
			"doctype": "Invoice Batch",
			"source_doctype": source_doctype,
			"source_name": source_name,
			"customer": source.customer,
			"customer_name": source.customer_name,
			"vehicle": source.get("vehicle"),
			"counter_session": session,
			"posting_date": nowdate(),
			"request_key": request_key,
			"status": "In Progress",
			"payments": payments,
			"change_due": change_due,
			"requested_by": frappe.session.user,
		}
	)
	for company, clines in by_company.items():
		batch.append(
			"companies",
			{
				"company": company,
				"status": "Pending",
				"line_count": len(clines),
				"net_total": flt(drafts[company].net_total),
				"grand_total": invoice_total(drafts[company]),
				"lines_json": json.dumps([{"row": line["row"].name, "qty": line["qty"]} for line in clines]),
			},
		)
	batch.insert(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep: the batch must survive a failure in a later company

	# 6. per-company submit
	_submit_companies(batch, source, drafts)

	# 7. payments
	if payments:
		payment_service.allocate_and_post(batch, payments, change_due)

	# 8. finalise
	return finalise(batch.name)


def _submit_companies(batch, source, drafts: dict | None = None):
	frappe.flags.cc_billing_in_progress = True
	try:
		for comp in batch.companies:
			if comp.status != "Pending" and comp.status != "Failed":
				continue
			# never recreate an invoice that already exists for this batch + company
			done = frappe.db.get_value(
				"Sales Invoice",
				{"cc_invoice_batch": batch.name, "company": comp.company, "docstatus": 1, "is_return": 0},
				["name", "grand_total", "rounded_total"],
				as_dict=True,
			)
			if done:
				comp.status = "Submitted"
				comp.sales_invoice = done.name
				comp.grand_total = flt(done.rounded_total) or flt(done.grand_total)
				comp.error = None
				continue

			frappe.db.savepoint("cc_company_invoice")
			comp.attempts = cint(comp.attempts) + 1
			try:
				si = (drafts or {}).get(comp.company)
				if si is None:
					si = _rebuild_from_lines(source, comp)
				si.cc_invoice_batch = batch.name
				si.flags.ignore_permissions = True
				si.insert()
				si.submit()
				comp.status = "Submitted"
				comp.sales_invoice = si.name
				comp.grand_total = invoice_total(si)
				comp.net_total = flt(si.net_total)
				comp.error = None
			except Exception as e:
				frappe.db.rollback(save_point="cc_company_invoice")
				comp.status = "Failed"
				comp.error = _clean_error(e)
				frappe.log_error(
					title=f"Car Cupid invoice failed: {batch.name} / {comp.company}",
					reference_doctype="Invoice Batch",
					reference_name=batch.name,
				)
			comp.db_update()
			frappe.db.commit()  # nosemgrep: each company's invoice is committed independently
	finally:
		frappe.flags.cc_billing_in_progress = False


def _rebuild_from_lines(source, comp):
	"""Rebuild a failed company's invoice from the lines it was meant to bill, re-checking eligibility."""
	source = frappe.get_doc(source.doctype, source.name)
	wanted = {d["row"]: flt(d["qty"]) for d in json.loads(comp.lines_json or "[]")}
	lines, _blockers = eligible_lines(source)
	lines = [
		{**line, "qty": min(line["qty"], wanted[line["row"].name])}
		for line in lines
		if line["row"].name in wanted and line["row"].company == comp.company
	]
	if not lines:
		frappe.throw(_("Nothing left to bill for {0} on this batch").format(comp.company))
	return build_invoice(source, comp.company, lines)


def _clean_error(e: Exception) -> str:
	msg = str(e) or e.__class__.__name__
	if frappe.message_log:
		try:
			last = frappe.message_log[-1]
			last = json.loads(last) if isinstance(last, str) else last
			msg = last.get("message") or msg
		except Exception:
			pass
		frappe.clear_messages()
	return frappe.utils.strip_html(msg)[:1000]


def finalise(batch_name: str) -> dict:
	from car_cupid_service.services.source_status import refresh_source

	batch = frappe.get_doc("Invoice Batch", batch_name)
	statuses = [c.status for c in batch.companies]
	live = [c for c in batch.companies if c.status == "Submitted"]
	if statuses and all(s == "Cancelled" for s in statuses):
		batch.status = "Cancelled"
	elif statuses and all(s in ("Submitted", "Cancelled") for s in statuses):
		batch.status = "Submitted"
	elif live:
		batch.status = "Partially Submitted"
	else:
		batch.status = "Failed"
	batch.total_amount = sum(flt(c.grand_total) for c in live)
	batch.paid_amount = sum(flt(a.amount) for a in batch.allocations if a.status == "Posted")
	outstanding = 0.0
	for c in live:
		outstanding += flt(frappe.db.get_value("Sales Invoice", c.sales_invoice, "outstanding_amount"))
	batch.outstanding_amount = outstanding
	batch.flags.ignore_permissions = True
	batch.save()

	src = refresh_source(batch.source_doctype, batch.source_name)
	if src and src.doctype == "Counter Cash Memo":
		_sync_memo_payments(src, batch)
	frappe.db.commit()  # nosemgrep
	return batch_summary(batch.name)


def _sync_memo_payments(memo, batch):
	memo.invoice_batch = batch.name
	if batch.payments and not memo.payments:
		for p in batch.payments:
			memo.append(
				"payments",
				{"mode_of_payment": p.mode_of_payment, "amount": p.amount, "reference_no": p.reference_no},
			)
	memo.change_returned = flt(batch.change_due)
	memo.flags.from_system = True
	memo.flags.ignore_permissions = True
	memo.save()


def retry_batch(batch_name: str) -> dict:
	_check_billing_roles()
	frappe.db.get_value("Invoice Batch", batch_name, "name", for_update=True)
	batch = frappe.get_doc("Invoice Batch", batch_name)
	if batch.status not in ("Failed", "Partially Submitted", "In Progress"):
		if any(a.status in ("Failed", "Pending") for a in batch.allocations):
			payment_service.allocate_and_post(batch, None, None)
			return finalise(batch.name)
		frappe.throw(_("Nothing to retry on batch {0} ({1})").format(batch.name, batch.status))
	batch.status = "In Progress"
	batch.db_update()
	frappe.db.commit()  # nosemgrep
	source = frappe.get_doc(batch.source_doctype, batch.source_name)
	_submit_companies(batch, source, None)
	if batch.payments:
		payment_service.allocate_and_post(batch, None, flt(batch.change_due))
	return finalise(batch.name)


def recover_stuck_batches():
	"""Scheduler: a batch left 'In Progress' (worker crash, timeout) is finalised from what really exists."""
	cutoff = add_to_date(now_datetime(), minutes=-15)
	for name in frappe.get_all(
		"Invoice Batch", filters={"status": "In Progress", "modified": ("<", cutoff)}, pluck="name"
	):
		try:
			batch = frappe.get_doc("Invoice Batch", name)
			for comp in batch.companies:
				done = frappe.db.get_value(
					"Sales Invoice",
					{"cc_invoice_batch": batch.name, "company": comp.company, "docstatus": 1, "is_return": 0},
					"name",
				)
				if done:
					comp.status, comp.sales_invoice = "Submitted", done
				elif comp.status == "Pending":
					comp.status, comp.error = (
						"Failed",
						_("Interrupted before the invoice was submitted. Retry."),
					)
				comp.db_update()
			finalise(name)
		except Exception:
			frappe.log_error(title=f"Car Cupid: could not recover batch {name}")
