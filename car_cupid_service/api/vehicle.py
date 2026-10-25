import frappe
from frappe import _

from car_cupid_service.car_cupid_service.doctype.customer_vehicle.customer_vehicle import (
	normalize_vehicle_number,
)


@frappe.whitelist()
def lookup(vehicle_number: str) -> dict | None:
	frappe.has_permission("Customer Vehicle", "read", throw=True)
	number = normalize_vehicle_number(vehicle_number)
	if not number or not frappe.db.exists("Customer Vehicle", number):
		return None
	v = frappe.get_doc("Customer Vehicle", number)
	open_job = frappe.db.get_value(
		"Service Job Card",
		{"vehicle": number, "status": ("not in", ["Billed", "Delivered", "Cancelled"])},
		"name",
	)
	return {**v.as_dict(), "open_job_card": open_job}


@frappe.whitelist()
def search_or_create(
	vehicle_number: str, customer: str, make=None, model=None, colour=None, fuel_type=None
) -> dict:
	"""Find a vehicle by number or register it for the customer. Never silently changes the owner."""
	number = normalize_vehicle_number(vehicle_number)
	if not number:
		frappe.throw(_("Vehicle number is required"))
	if frappe.db.exists("Customer Vehicle", number):
		v = frappe.get_doc("Customer Vehicle", number)
		v.check_permission("read")
		result = v.as_dict()
		if v.customer != customer:
			result["owner_mismatch"] = True
			result["message"] = _(
				"{0} is registered to {1}. Update the owner only if the vehicle was sold."
			).format(number, v.customer_name)
		return result
	frappe.has_permission("Customer Vehicle", "create", throw=True)
	v = frappe.get_doc(
		{
			"doctype": "Customer Vehicle",
			"vehicle_number": number,
			"customer": customer,
			"make": make,
			"model": model,
			"colour": colour,
			"fuel_type": fuel_type,
		}
	)
	v.insert()
	return v.as_dict()


@frappe.whitelist()
def transfer_owner(vehicle: str, customer: str) -> dict:
	v = frappe.get_doc("Customer Vehicle", vehicle)
	v.check_permission("write")
	v.customer = customer
	v.mobile_no = None
	v.email_id = None
	v.save()
	return v.as_dict()
