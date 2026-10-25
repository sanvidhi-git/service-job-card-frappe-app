"""doc_events on ERPNext Sales Invoice / Payment Entry."""

import frappe

from car_cupid_service.services import incentive_service
from car_cupid_service.services.source_status import refresh_source


def _source_of(si):
	if si.get("cc_source_doctype") and si.get("cc_source_name"):
		return si.cc_source_doctype, si.cc_source_name
	if si.get("is_return") and si.get("return_against"):
		return frappe.db.get_value("Sales Invoice", si.return_against, ["cc_source_doctype", "cc_source_name"]) or (None, None)
	if any(d.get("cc_source_row") for d in si.items):
		row = next(d.cc_source_row for d in si.items if d.get("cc_source_row"))
		for dt, child in (("Service Job Card", "Service Job Card Item"), ("Counter Cash Memo", "Counter Cash Memo Item")):
			parent = frappe.db.get_value(child, row, "parent")
			if parent:
				return dt, parent
	return None, None


def on_sales_invoice_submit(doc, method=None):
	incentive_service.on_invoice_submit(doc)
	if frappe.flags.get("cc_billing_in_progress"):
		return  # the billing engine refreshes the source once, at the end
	refresh_source(*_source_of(doc))


def on_sales_invoice_cancel(doc, method=None):
	incentive_service.on_invoice_cancel(doc)
	if doc.get("cc_invoice_batch") and frappe.db.exists("Invoice Batch", doc.cc_invoice_batch):
		batch = frappe.get_doc("Invoice Batch", doc.cc_invoice_batch)
		for comp in batch.companies:
			if comp.sales_invoice == doc.name:
				comp.status = "Cancelled"
				comp.db_update()
		for alloc in batch.allocations:
			if alloc.sales_invoice == doc.name and alloc.status in ("Pending", "Failed"):
				alloc.status = "Cancelled"
				alloc.db_update()
		batch.reload()
		statuses = {c.status for c in batch.companies}
		if statuses == {"Cancelled"}:
			batch.db_set("status", "Cancelled")
		batch.add_comment("Info", f"Sales Invoice {doc.name} ({doc.company}) cancelled by {frappe.session.user}")
	refresh_source(*_source_of(doc))


def on_payment_entry_cancel(doc, method=None):
	if not doc.get("cc_invoice_batch"):
		return
	for name in frappe.get_all(
		"Counter Payment Allocation",
		filters={"parent": doc.cc_invoice_batch, "payment_entry": doc.name},
		pluck="name",
	):
		frappe.db.set_value("Counter Payment Allocation", name, "status", "Cancelled")
