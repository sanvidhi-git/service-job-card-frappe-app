"""Counter session endpoints: open, cash movements, closing count."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from car_cupid_service.services.payment_service import get_open_session
from car_cupid_service.services.settings import denominations, get_settings, is_supervisor


def _session_payload(doc) -> dict:
	doc.compute_summary()
	doc.compute_closing()
	d = doc.as_dict()
	d["tolerance"] = flt(get_settings().cash_variance_tolerance)
	d["is_supervisor"] = is_supervisor()
	return d


@frappe.whitelist()
def get_session(name: str | None = None) -> dict | None:
	name = name or get_open_session()
	if not name:
		return None
	doc = frappe.get_doc("Counter Session", name)
	doc.check_permission("read")
	return _session_payload(doc)


@frappe.whitelist()
def open_session(counter_name: str, opening_float: float = 0) -> dict:
	frappe.has_permission("Counter Session", "create", throw=True)
	existing = get_open_session()
	if existing:
		return _session_payload(frappe.get_doc("Counter Session", existing))
	doc = frappe.get_doc(
		{
			"doctype": "Counter Session",
			"counter_name": counter_name or _("Main Counter"),
			"cashier": frappe.session.user,
			"opening_float": flt(opening_float),
		}
	)
	doc.insert()
	return _session_payload(doc)


@frappe.whitelist()
def add_movement(session: str, movement_type: str, amount: float, reason: str) -> dict:
	doc = frappe.get_doc("Counter Session", session)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Session {0} is closed").format(session))
	if not (reason or "").strip():
		frappe.throw(_("A reason is required for every cash movement"))
	doc.append("movements", {"movement_type": movement_type, "amount": flt(amount), "reason": reason})
	doc.save()
	return _session_payload(doc)


@frappe.whitelist()
def close_session(
	session: str,
	counts: str | dict | None = None,
	physical_cash: float | None = None,
	variance_reason: str | None = None,
	approve_variance: int = 0,
) -> dict:
	"""Save the count; submit (close) when within tolerance or approved, else park as Pending Review."""
	doc = frappe.get_doc("Counter Session", session)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Session {0} is already closed").format(session))
	counts = frappe.parse_json(counts) if counts else {}
	if counts:
		doc.set("closing_denominations", [])
		for d in denominations():
			key = str(int(d)) if float(d).is_integer() else str(d)
			doc.append(
				"closing_denominations",
				{"denomination": d, "count": cint(counts.get(key) or counts.get(str(d)) or 0)},
			)
	elif physical_cash is not None:
		doc.physical_cash = flt(physical_cash)
	if variance_reason:
		doc.variance_reason = variance_reason
	if cint(approve_variance):
		if not is_supervisor():
			frappe.throw(_("Only a Billing Supervisor or Accounts Manager can approve a cash variance"))
		doc.variance_approved = 1
		doc.variance_approved_by = frappe.session.user
	doc.compute_summary()
	doc.compute_closing()
	tolerance = flt(get_settings().cash_variance_tolerance)
	if abs(flt(doc.variance)) > tolerance and not cint(doc.variance_approved):
		doc.status = "Pending Review"
		doc.save()
		result = _session_payload(doc)
		result["needs_approval"] = True
		return result
	doc.save()
	doc.submit()
	return _session_payload(doc)
