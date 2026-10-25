"""Derive line quantities and document statuses from the real source of truth.

Quantities are never typed in: requested/issued/returned come from Material Issue Requests and
billed quantities come from submitted Sales Invoice rows (credit notes subtract automatically).
Every save recomputes them, so a missed hook can never leave a job card out of sync.
"""

import frappe
from frappe.utils import cint, flt

from car_cupid_service.services.settings import get_settings


def _billed_by_row(source_doctype: str, source_name: str, row_names: list[str]) -> dict:
	if not row_names:
		return {}
	rows = frappe.db.sql(
		"""
		select sii.cc_source_row as row_name, sum(sii.qty) as qty,
			max(case when si.is_return = 0 then si.name end) as invoice,
			max(case when si.is_return = 0 then sii.name end) as invoice_item
		from `tabSales Invoice Item` sii
		join `tabSales Invoice` si on si.name = sii.parent
		where si.docstatus = 1 and sii.cc_source_row in %(rows)s
		group by sii.cc_source_row
		""",
		{"rows": tuple(row_names)},
		as_dict=True,
	)
	return {r.row_name: r for r in rows}


def _material_by_row(job_card: str) -> dict:
	rows = frappe.db.sql(
		"""
		select job_card_item as row_name,
			sum(case when status != 'Cancelled' then requested_qty else issued_qty end) as requested_qty,
			sum(issued_qty) as issued_qty, sum(returned_qty) as returned_qty
		from `tabMaterial Issue Request`
		where job_card = %s
		group by job_card_item
		""",
		job_card,
		as_dict=True,
	)
	return {r.row_name: r for r in rows}


def _invoices(source_doctype: str, source_name: str) -> list[dict]:
	if not source_name:
		return []
	return frappe.get_all(
		"Sales Invoice",
		filters={
			"cc_source_doctype": source_doctype,
			"cc_source_name": source_name,
			"docstatus": ("in", [1, 2]),
		},
		fields=[
			"name",
			"company",
			"grand_total",
			"rounded_total",
			"status",
			"docstatus",
			"posting_date",
			"cc_invoice_batch",
		],
		order_by="creation asc",
	)


def refresh_quantities(doc):
	is_job = doc.doctype == "Service Job Card"
	if doc.is_new():
		return
	billed = _billed_by_row(doc.doctype, doc.name, [r.name for r in doc.items if r.name])
	material = _material_by_row(doc.name) if is_job else {}
	policy = get_settings().material_issue_policy

	for row in doc.items:
		b = billed.get(row.name)
		row.billed_qty = flt(b.qty) if b else 0
		row.sales_invoice = (b and b.invoice) or None
		row.sales_invoice_item = (b and b.invoice_item) or None
		if not is_job:
			continue
		m = material.get(row.name)
		row.requested_qty = flt(m.requested_qty) if m else 0
		row.issued_qty = flt(m.issued_qty) if m else 0
		row.returned_qty = flt(m.returned_qty) if m else 0
		net_issued = row.issued_qty - row.returned_qty
		if not cint(row.is_stock_item) or policy == "Not Required":
			row.fulfilment_status = "Not Required"
		elif net_issued >= flt(row.qty) and flt(row.qty) > 0:
			row.fulfilment_status = "Issued"
		elif row.issued_qty > 0:
			row.fulfilment_status = "Partially Issued"
		elif row.requested_qty > 0:
			row.fulfilment_status = "Requested"
		else:
			row.fulfilment_status = "Pending"

	_refresh_bill_discounts(doc)
	invoices = _invoices(doc.doctype, doc.name)
	doc.set("invoices", [])
	for inv in invoices:
		doc.append(
			"invoices",
			{
				"company": inv.company,
				"sales_invoice": inv.name,
				"grand_total": inv.rounded_total or inv.grand_total,
				"status": "Cancelled" if inv.docstatus == 2 else inv.status,
				"invoice_batch": inv.cc_invoice_batch,
				"posting_date": inv.posting_date,
			},
		)
	live = sum(flt(i.rounded_total or i.grand_total) for i in invoices if i.docstatus == 1)
	if is_job:
		doc.total_billed_amount = live
	else:
		doc.grand_total = live


def _refresh_bill_discounts(doc):
	"""A discount row is Applied while a submitted invoice lists it in `cc_discount_rows`."""
	rows = doc.get("bill_discounts") or []
	if not rows:
		return
	applied = {}
	for inv in frappe.get_all(
		"Sales Invoice",
		filters={
			"cc_source_doctype": doc.doctype,
			"cc_source_name": doc.name,
			"docstatus": 1,
			"is_return": 0,
		},
		fields=["name", "cc_discount_rows"],
	):
		for row_name in (inv.cc_discount_rows or "").split(","):
			if row_name.strip():
				applied[row_name.strip()] = inv.name
	for r in rows:
		r.sales_invoice = applied.get(r.name)
		r.status = "Applied" if r.sales_invoice else "Pending"


def _billing_status(rows) -> str:
	active = [r for r in rows if getattr(r, "work_status", None) != "Cancelled"]
	if not active:
		return "Not Billed"
	billed = [flt(r.billed_qty) for r in active]
	if all(flt(r.billed_qty) >= flt(r.qty) for r in active):
		return "Fully Billed"
	if any(b > 0 for b in billed):
		return "Partially Billed"
	return "Not Billed"


def set_job_card_status(doc):
	active = [r for r in doc.items if r.work_status != "Cancelled"]
	if active and all(r.work_status == "Completed" for r in active):
		doc.work_status = "Completed"
	elif any(r.work_status in ("In Progress", "Completed") for r in active):
		doc.work_status = "In Progress"
	else:
		doc.work_status = "Not Started"

	stock = [r for r in active if r.fulfilment_status != "Not Required"]
	if not stock:
		doc.fulfilment_status = "Not Required"
	elif all(r.fulfilment_status == "Issued" for r in stock):
		doc.fulfilment_status = "Issued"
	elif any(r.fulfilment_status in ("Issued", "Partially Issued") for r in stock):
		doc.fulfilment_status = "Partially Issued"
	else:
		doc.fulfilment_status = "Pending"

	doc.billing_status = _billing_status(doc.items)

	if doc.status == "Cancelled":
		return
	if doc.delivery_status == "Delivered":
		doc.status = "Delivered"
	elif doc.billing_status == "Fully Billed":
		doc.status = "Billed"
	elif doc.billing_status == "Partially Billed":
		doc.status = "Partially Billed"
	elif active and is_ready_for_billing(doc):
		doc.status = "Ready for Billing"
	elif doc.work_status != "Not Started" or doc.fulfilment_status in ("Issued", "Partially Issued"):
		doc.status = "Work In Progress"
	else:
		doc.status = "Open"


def is_ready_for_billing(doc) -> bool:
	settings = get_settings()
	issue_required = settings.material_issue_policy == "Issue Required for Stock Items"
	for r in doc.items:
		if r.work_status == "Cancelled":
			continue
		if cint(r.is_stock_item):
			if issue_required and r.fulfilment_status != "Issued":
				return False
		elif cint(settings.require_work_completion) and r.work_status != "Completed":
			return False
	return True


def set_memo_status(doc):
	if doc.status == "Cancelled":
		return
	billing = _billing_status(doc.items)
	doc.status = {"Fully Billed": "Billed", "Partially Billed": "Partially Billed"}.get(billing, "Draft")


def refresh_source(source_doctype: str | None, source_name: str | None):
	"""Reload a job card / cash memo and persist recomputed quantities and statuses."""
	if not source_doctype or not source_name or not frappe.db.exists(source_doctype, source_name):
		return
	doc = frappe.get_doc(source_doctype, source_name)
	doc.flags.from_system = True
	doc.flags.ignore_permissions = True
	doc.flags.ignore_links = True
	doc.save()
	return doc
