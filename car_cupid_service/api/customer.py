import re

import frappe
from frappe import _
from frappe.utils import cint

from car_cupid_service.services.settings import get_settings


def _digits(value: str | None) -> str:
	return re.sub(r"\D", "", value or "")


@frappe.whitelist()
def search(txt: str, limit: int = 10) -> list[dict]:
	"""Search customers by name, mobile number or vehicle number."""
	frappe.has_permission("Customer", "read", throw=True)
	txt = (txt or "").strip()
	if len(txt) < 2:
		return []
	limit = min(cint(limit) or 10, 25)
	like = f"%{txt}%"
	digits = _digits(txt)
	results = {}
	conds = [["customer_name", "like", like], ["name", "like", like]]
	if len(digits) >= 4:
		conds.append(["mobile_no", "like", f"%{digits[-10:]}%"])
	for c in frappe.get_list(
		"Customer",
		or_filters=conds,
		filters={"disabled": 0},
		fields=["name", "customer_name", "mobile_no", "email_id"],
		limit_page_length=limit,
	):
		results[c.name] = {**c, "vehicles": []}

	vnorm = re.sub(r"[^A-Z0-9]", "", txt.upper())
	if len(vnorm) >= 3:
		for v in frappe.get_list(
			"Customer Vehicle",
			filters={"vehicle_number": ("like", f"%{vnorm}%"), "status": "Active"},
			fields=["name", "customer", "customer_name", "make", "model"],
			limit_page_length=limit,
		):
			entry = results.setdefault(
				v.customer,
				{
					"name": v.customer,
					"customer_name": v.customer_name,
					"mobile_no": frappe.db.get_value("Customer", v.customer, "mobile_no"),
					"vehicles": [],
				},
			)
			entry["matched_vehicle"] = v.name
	for name, entry in results.items():
		entry["vehicles"] = frappe.get_list(
			"Customer Vehicle",
			filters={"customer": name, "status": "Active"},
			fields=["name", "make", "model", "colour", "last_odometer"],
			limit_page_length=20,
		)
	return list(results.values())[:limit]


@frappe.whitelist()
def quick_create(customer_name: str, mobile_no: str, email_id: str | None = None) -> dict:
	"""Create a customer with mobile number; returns the existing one if the mobile is already known."""
	frappe.has_permission("Customer", "create", throw=True)
	customer_name = (customer_name or "").strip()
	mobile = _digits(mobile_no)
	if not customer_name:
		frappe.throw(_("Customer name is required"))
	if len(mobile) < 10:
		frappe.throw(_("Enter a valid 10-digit mobile number"))
	mobile = mobile[-10:]
	existing = frappe.db.get_value(
		"Customer", {"mobile_no": ("like", f"%{mobile}")}, ["name", "customer_name", "mobile_no"], as_dict=True
	)
	if existing:
		existing["existing"] = True
		return existing

	settings = get_settings()
	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": customer_name,
			"customer_type": "Individual",
			"customer_group": settings.default_customer_group
			or frappe.db.get_single_value("Selling Settings", "customer_group")
			or frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
			"territory": settings.default_territory
			or frappe.db.get_single_value("Selling Settings", "territory")
			or frappe.db.get_value("Territory", {"is_group": 0}, "name"),
		}
	)
	customer.insert()
	contact = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": customer_name,
			"links": [{"link_doctype": "Customer", "link_name": customer.name}],
		}
	)
	contact.add_phone(mobile, is_primary_mobile_no=1)
	if email_id:
		contact.add_email(email_id, is_primary=1)
	contact.insert()
	customer.db_set({"customer_primary_contact": contact.name, "mobile_no": mobile, "email_id": email_id})
	return {"name": customer.name, "customer_name": customer.customer_name, "mobile_no": mobile, "existing": False}
