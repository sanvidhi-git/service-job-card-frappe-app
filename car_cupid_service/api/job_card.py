"""Job card endpoints for the POS page (the standard form uses the same controller rules)."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from car_cupid_service.services import billing_service, line_service, stock_service
from car_cupid_service.services.incentive_service import estimate_for_line


def _get(job_card: str, ptype: str = "read"):
	doc = frappe.get_doc("Service Job Card", job_card)
	doc.check_permission(ptype)
	return doc


def payload(doc) -> dict:
	d = doc.as_dict()
	d["company_summary"] = line_service.company_summary(doc)
	d["material_requests"] = frappe.get_all(
		"Material Issue Request",
		filters={"job_card": doc.name},
		fields=["name", "job_card_item", "item_code", "item_name", "requested_qty", "issued_qty", "returned_qty", "pending_qty", "status"],
		order_by="creation asc",
	)
	d["ready_for_billing"] = doc.status in ("Ready for Billing", "Partially Billed")
	d["estimated_incentive"] = sum(estimate_for_line(r, r.company) for r in doc.items if r.work_status != "Cancelled")
	return d


@frappe.whitelist()
def get(job_card: str) -> dict:
	return payload(_get(job_card))


@frappe.whitelist()
def list_jobs(status: str | None = None, txt: str | None = None, limit: int = 50) -> list[dict]:
	filters = {}
	if status == "active" or not status:
		filters["status"] = ("not in", ["Delivered", "Cancelled"])
	elif status != "all":
		filters["status"] = status
	or_filters = None
	if txt:
		like = f"%{txt}%"
		or_filters = [["vehicle_number", "like", like], ["customer_name", "like", like], ["mobile_no", "like", like], ["name", "like", like]]
	return frappe.get_list(
		"Service Job Card",
		filters=filters,
		or_filters=or_filters,
		fields=[
			"name",
			"customer_name",
			"vehicle_number",
			"vehicle_description",
			"status",
			"work_status",
			"fulfilment_status",
			"billing_status",
			"priority",
			"promised_delivery",
			"net_total",
			"posting_datetime",
		],
		order_by="modified desc",
		limit_page_length=min(cint(limit) or 50, 200),
	)


@frappe.whitelist()
def create(data: str | dict) -> dict:
	"""Quick intake from the POS. Refuses a second open job card for the same vehicle."""
	data = frappe.parse_json(data)
	frappe.has_permission("Service Job Card", "create", throw=True)
	if not data.get("vehicle"):
		frappe.throw(_("Select or register the vehicle"))
	open_job = frappe.db.get_value(
		"Service Job Card",
		{"vehicle": data["vehicle"], "status": ("not in", ["Billed", "Delivered", "Cancelled"])},
		"name",
	)
	if open_job and not cint(data.get("force_new")):
		return {"duplicate": True, "name": open_job}
	allowed = {
		"customer",
		"vehicle",
		"odometer",
		"fuel_level",
		"customer_complaint",
		"special_instructions",
		"belongings_noted",
		"damage_notes",
		"priority",
		"promised_delivery",
		"service_advisor",
		"default_sales_person",
		"default_technician",
		"customer_acknowledged",
	}
	doc = frappe.new_doc("Service Job Card")
	doc.update({k: v for k, v in data.items() if k in allowed})
	doc.default_sales_person = doc.default_sales_person or line_service.get_user_sales_person()
	from car_cupid_service.services.payment_service import get_open_session

	doc.counter_session = get_open_session()
	for line in data.get("items") or []:
		doc.append("items", _line_from(line))
	doc.insert()
	return payload(doc)


def _line_from(line: dict) -> dict:
	return {
		"item_code": line.get("item_code"),
		# explicit routing: child rows would otherwise inherit the user's default company
		"company": line.get("company") or line_service.route_company(line.get("item_code")),
		"qty": flt(line.get("qty")) or 1,
		"uom": line.get("uom"),
		"price_list_rate": flt(line.get("price_list_rate")),
		"discount_percentage": flt(line.get("discount_percentage")),
		"sales_person": line.get("sales_person"),
		"technician": line.get("technician"),
		"item_type": line.get("item_type"),
		"remarks": line.get("remarks"),
	}


@frappe.whitelist()
def add_line(job_card: str, line: str | dict) -> dict:
	doc = _get(job_card, "write")
	doc.append("items", _line_from(frappe.parse_json(line)))
	doc.save()
	return payload(doc)


EDITABLE_LINE_FIELDS = {
	"qty",
	"company",
	"price_list_rate",
	"discount_percentage",
	"sales_person",
	"technician",
	"work_status",
	"remarks",
	"item_type",
}


@frappe.whitelist()
def update_line(job_card: str, row: str, values: str | dict) -> dict:
	doc = _get(job_card, "write")
	values = frappe.parse_json(values)
	target = next((r for r in doc.items if r.name == row), None)
	if not target:
		frappe.throw(_("Line not found; refresh the job card"))
	for k, v in values.items():
		if k in EDITABLE_LINE_FIELDS:
			target.set(k, v)
	if "company" in values:
		target.warehouse = None
		target.price_list_rate = 0
	doc.save()
	return payload(doc)


@frappe.whitelist()
def remove_line(job_card: str, row: str) -> dict:
	doc = _get(job_card, "write")
	target = next((r for r in doc.items if r.name == row), None)
	if not target:
		frappe.throw(_("Line not found; refresh the job card"))
	if flt(target.billed_qty) or flt(target.issued_qty) - flt(target.returned_qty) > 0 or flt(target.requested_qty):
		frappe.throw(
			_("This line has material requested/issued or is billed. Mark it Cancelled instead of deleting it."),
			title=_("Cannot Remove"),
		)
	doc.remove(target)
	doc.save()
	return payload(doc)


@frappe.whitelist()
def set_work_status(job_card: str, rows: str | list | None = None, status: str = "Completed") -> dict:
	doc = _get(job_card, "write")
	doc.set_work_status(rows, status)
	return payload(doc)


@frappe.whitelist()
def request_materials(job_card: str, rows: str | list | None = None) -> dict:
	_get(job_card, "read")
	rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
	stock_service.request_materials(job_card, rows or None)
	return payload(_get(job_card))


@frappe.whitelist()
def checkout(job_card: str, payments: str | list, request_key: str, allow_credit: int = 0) -> dict:
	result = billing_service.bill_source(
		"Service Job Card", job_card, request_key, frappe.parse_json(payments), bool(cint(allow_credit))
	)
	return result


@frappe.whitelist()
def mark_delivered(job_card: str) -> dict:
	doc = _get(job_card, "write")
	doc.mark_delivered()
	return payload(doc)


@frappe.whitelist()
def cancel_job(job_card: str, reason: str) -> dict:
	doc = _get(job_card, "write")
	doc.cancel_job(reason)
	return payload(doc)
