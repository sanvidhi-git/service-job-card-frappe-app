"""Material request, store issue and return for job cards.

Stock-posting model (prevents double deduction by design):
  * Request  -> Material Request (type Material Transfer), store -> service bay
  * Issue    -> Stock Entry (Material Transfer) store -> service bay. Company stock is unchanged,
                the parts are now physically "on the job".
  * Sale     -> Sales Invoice with Update Stock, deducting from the service bay warehouse.
  * Return   -> Stock Entry (Material Transfer) service bay -> store.
Each unit leaves company stock exactly once: on the invoice.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, now_datetime, nowdate

from car_cupid_service.services.settings import get_company_row, get_settings, material_flow_enabled

STORE_ROLES = {"Storekeeper", "Billing Supervisor", "System Manager", "Stock Manager"}
REQUEST_ROLES = {
	"Service Engineer",
	"Service Advisor",
	"Counter Salesperson",
	"Billing Supervisor",
	"System Manager",
}


def _check_roles(allowed: set, action: str):
	if not allowed.intersection(frappe.get_roles()):
		frappe.throw(_("You are not permitted to {0}.").format(action), frappe.PermissionError)


# ----------------------------------------------------------------------------- request
def request_materials(job_card: str, rows: list[str] | None = None, silent: bool = False) -> list[str]:
	"""Create Material Requests (grouped by company + warehouses) for unrequested stock quantities."""
	if not material_flow_enabled():
		if silent:
			return []
		frappe.throw(_("Material requests are switched off in Car Cupid Settings (policy: Not Required)."))
	if not silent:
		_check_roles(REQUEST_ROLES | STORE_ROLES, _("request material"))

	frappe.db.get_value("Service Job Card", job_card, "name", for_update=True)
	jc = frappe.get_doc("Service Job Card", job_card)
	if jc.status in ("Cancelled", "Delivered"):
		frappe.throw(_("Job card {0} is {1}").format(job_card, jc.status))

	groups: dict[tuple, list] = {}
	for row in jc.items:
		if rows and row.name not in rows:
			continue
		if not cint(row.is_stock_item) or row.work_status == "Cancelled":
			continue
		need = flt(row.qty) - flt(row.requested_qty)
		if need <= 0:
			continue
		company_row = get_company_row(row.company)
		if not company_row.service_bay_warehouse:
			frappe.throw(
				_("Set a Service Bay (WIP) Warehouse for {0} in Car Cupid Settings").format(row.company)
			)
		source = row.warehouse or company_row.default_warehouse
		groups.setdefault((row.company, source, company_row.service_bay_warehouse), []).append((row, need))

	if not groups:
		if not silent:
			frappe.msgprint(_("All stock lines are already requested."), indicator="blue", alert=True)
		return []

	lead = cint(get_settings().material_request_lead_days)
	created = []
	for (company, source, target), lines in groups.items():
		mr = frappe.new_doc("Material Request")
		mr.material_request_type = "Material Transfer"
		mr.company = company
		mr.transaction_date = nowdate()
		mr.schedule_date = add_days(nowdate(), lead)
		mr.set_from_warehouse = source
		mr.set_warehouse = target
		mr.cc_job_card = jc.name
		for row, need in lines:
			mr.append(
				"items",
				{
					"item_code": row.item_code,
					"qty": need,
					"uom": row.uom,
					"from_warehouse": source,
					"warehouse": target,
					"schedule_date": mr.schedule_date,
					"description": f"{row.item_name} — {jc.vehicle_number or ''} ({jc.name})",
				},
			)
		mr.flags.ignore_permissions = True
		mr.insert()
		mr.submit()

		for (row, need), mr_item in zip(lines, mr.items, strict=True):
			mir = frappe.get_doc(
				{
					"doctype": "Material Issue Request",
					"job_card": jc.name,
					"job_card_item": row.name,
					"vehicle_number": jc.vehicle_number,
					"customer_name": jc.customer_name,
					"company": company,
					"item_code": row.item_code,
					"item_name": row.item_name,
					"uom": row.uom,
					"source_warehouse": source,
					"target_warehouse": target,
					"requested_qty": need,
					"material_request": mr.name,
					"material_request_item": mr_item.name,
					"requested_by": frappe.session.user,
					"technician": row.technician,
				}
			)
			mir.insert(ignore_permissions=True)
			created.append(mir.name)

	from car_cupid_service.services.source_status import refresh_source

	refresh_source("Service Job Card", jc.name)
	if not silent:
		frappe.msgprint(
			_("{0} material request line(s) sent to stores.").format(len(created)),
			indicator="green",
			alert=True,
		)
	return created


# ----------------------------------------------------------------------------- issue / return
def _make_transfer(mir, qty: float, source: str, target: str, is_issue: bool) -> str:
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Transfer"
	se.purpose = "Material Transfer"
	se.company = mir.company
	se.from_warehouse = source
	se.to_warehouse = target
	se.cc_material_issue_request = mir.name
	se.cc_job_card = mir.job_card
	se.remarks = _("{0} for job card {1} ({2})").format(
		_("Issue") if is_issue else _("Return"), mir.job_card, mir.vehicle_number or ""
	)
	item = {
		"item_code": mir.item_code,
		"qty": qty,
		"uom": mir.uom,
		"s_warehouse": source,
		"t_warehouse": target,
	}
	if is_issue and mir.material_request:
		item["material_request"] = mir.material_request
		item["material_request_item"] = mir.material_request_item
	se.append("items", item)
	se.flags.ignore_permissions = True
	se.insert()
	se.submit()
	return se.name


def _line_billed_and_net(mir) -> tuple[float, float]:
	billed, issued, returned = frappe.db.get_value(
		"Service Job Card Item", mir.job_card_item, ["billed_qty", "issued_qty", "returned_qty"]
	) or (0, 0, 0)
	return flt(billed), flt(issued) - flt(returned)


def record_issue(
	mir_name: str, qty: float, received_by: str | None = None, remarks: str | None = None
) -> dict:
	_check_roles(STORE_ROLES, _("issue material from stores"))
	frappe.db.get_value("Material Issue Request", mir_name, "name", for_update=True)
	mir = frappe.get_doc("Material Issue Request", mir_name)
	qty = flt(qty)
	if mir.status == "Cancelled":
		frappe.throw(_("Request {0} is cancelled").format(mir.name))
	if qty <= 0:
		frappe.throw(_("Issue quantity must be greater than zero"))
	if qty > flt(mir.pending_qty) + 1e-9:
		frappe.throw(
			_("Only {0} {1} is pending on this request; you tried to issue {2}.").format(
				mir.pending_qty, mir.uom, qty
			),
			title=_("Over-issue Blocked"),
		)
	available = flt(
		frappe.db.get_value(
			"Bin", {"item_code": mir.item_code, "warehouse": mir.source_warehouse}, "actual_qty"
		)
	)
	if available < qty:
		frappe.throw(
			_("Only {0} in stock at {1}. Cannot issue {2}.").format(available, mir.source_warehouse, qty),
			title=_("Insufficient Stock"),
		)
	se = _make_transfer(mir, qty, mir.source_warehouse, mir.target_warehouse, is_issue=True)
	mir.append(
		"movements",
		{
			"movement_type": "Issue",
			"qty": qty,
			"stock_entry": se,
			"posted_by": frappe.session.user,
			"posted_on": now_datetime(),
			"received_by": received_by or mir.technician,
			"remarks": remarks,
		},
	)
	mir.save(ignore_permissions=True)
	_refresh_job(mir.job_card)
	return {"stock_entry": se, "status": mir.status, "pending_qty": mir.pending_qty}


def record_return(mir_name: str, qty: float, remarks: str | None = None) -> dict:
	_check_roles(STORE_ROLES, _("accept returns into stores"))
	frappe.db.get_value("Material Issue Request", mir_name, "name", for_update=True)
	mir = frappe.get_doc("Material Issue Request", mir_name)
	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("Return quantity must be greater than zero"))
	billed, line_net = _line_billed_and_net(mir)
	returnable = min(flt(mir.issued_qty) - flt(mir.returned_qty), line_net - billed)
	if qty > returnable + 1e-9:
		frappe.throw(
			_("At most {0} can be returned (issued, not yet returned and not yet invoiced).").format(
				max(returnable, 0)
			),
			title=_("Return Blocked"),
		)
	se = _make_transfer(mir, qty, mir.target_warehouse, mir.source_warehouse, is_issue=False)
	mir.append(
		"movements",
		{
			"movement_type": "Return",
			"qty": qty,
			"stock_entry": se,
			"posted_by": frappe.session.user,
			"posted_on": now_datetime(),
			"remarks": remarks,
		},
	)
	mir.save(ignore_permissions=True)
	_refresh_job(mir.job_card)
	return {"stock_entry": se, "status": mir.status}


def cancel_request(mir_name: str) -> str:
	"""Cancel the un-issued part of a request. Already-issued quantity stays (return it separately)."""
	mir = frappe.get_doc("Material Issue Request", mir_name)
	if mir.status == "Cancelled":
		return mir.status
	mir.requested_qty = flt(mir.issued_qty)
	mir.status = "Cancelled"
	mir.pending_qty = 0
	mir.flags.ignore_permissions = True
	mir.save()
	if mir.material_request:
		open_mirs = frappe.db.count(
			"Material Issue Request",
			{
				"material_request": mir.material_request,
				"status": ("not in", ["Cancelled"]),
				"name": ("!=", mir.name),
			},
		)
		if not open_mirs:
			try:
				mr = frappe.get_doc("Material Request", mir.material_request)
				if mr.docstatus == 1 and mr.status not in ("Stopped", "Transferred", "Cancelled"):
					mr.flags.ignore_permissions = True
					mr.update_status("Stopped")
			except Exception:
				frappe.log_error(title=f"Car Cupid: could not stop Material Request {mir.material_request}")
	_refresh_job(mir.job_card)
	return mir.status


def _refresh_job(job_card: str):
	from car_cupid_service.services.source_status import refresh_source

	refresh_source("Service Job Card", job_card)


def on_stock_entry_cancel(doc, method=None):
	"""A cancelled issue/return stock entry is excluded from the request totals (history is kept)."""
	if not doc.get("cc_material_issue_request"):
		return
	mir = frappe.get_doc("Material Issue Request", doc.cc_material_issue_request)
	for m in mir.movements:
		if m.stock_entry == doc.name:
			m.remarks = ((m.remarks or "") + " " + _("[Stock Entry cancelled]")).strip()
	mir.flags.ignore_permissions = True
	mir.save()
	_refresh_job(mir.job_card)
