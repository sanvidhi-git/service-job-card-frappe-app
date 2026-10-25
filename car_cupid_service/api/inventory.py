"""Stores fulfilment endpoints."""

import frappe
from frappe.utils import cint, flt

from car_cupid_service.services import stock_service


@frappe.whitelist()
def pending_requests(
	company: str | None = None, warehouse: str | None = None, include_issued: int = 0
) -> list[dict]:
	frappe.has_permission("Material Issue Request", "read", throw=True)
	statuses = ["Requested", "Partially Issued"]
	if cint(include_issued):
		statuses.append("Issued")
	filters = {"status": ("in", statuses)}
	if company:
		filters["company"] = company
	if warehouse:
		filters["source_warehouse"] = warehouse
	rows = frappe.get_list(
		"Material Issue Request",
		filters=filters,
		fields=[
			"name",
			"job_card",
			"vehicle_number",
			"customer_name",
			"company",
			"item_code",
			"item_name",
			"uom",
			"source_warehouse",
			"target_warehouse",
			"requested_qty",
			"issued_qty",
			"returned_qty",
			"pending_qty",
			"status",
			"technician",
			"creation",
		],
		order_by="creation asc",
		limit_page_length=500,
	)
	for r in rows:
		r["in_stock"] = flt(
			frappe.db.get_value(
				"Bin", {"item_code": r.item_code, "warehouse": r.source_warehouse}, "actual_qty"
			)
		)
	return rows


@frappe.whitelist()
def record_issue(mir: str, qty: float, received_by: str | None = None, remarks: str | None = None) -> dict:
	return stock_service.record_issue(mir, flt(qty), received_by, remarks)


@frappe.whitelist()
def issue_all(job_card: str) -> dict:
	"""Issue every pending request of a job card that has stock available; reports what was skipped."""
	done, skipped = [], []
	for mir in frappe.get_all(
		"Material Issue Request",
		filters={"job_card": job_card, "status": ("in", ["Requested", "Partially Issued"])},
		fields=["name", "pending_qty", "item_code", "source_warehouse"],
	):
		try:
			stock_service.record_issue(mir.name, mir.pending_qty)
			done.append(mir.name)
		except frappe.ValidationError as e:
			frappe.clear_messages()
			skipped.append({"name": mir.name, "item_code": mir.item_code, "reason": str(e)})
	return {"issued": done, "skipped": skipped}


@frappe.whitelist()
def record_return(mir: str, qty: float, remarks: str | None = None) -> dict:
	return stock_service.record_return(mir, flt(qty), remarks)


@frappe.whitelist()
def cancel_request(mir: str) -> str:
	doc = frappe.get_doc("Material Issue Request", mir)
	doc.check_permission("write")
	return stock_service.cancel_request(mir)
