# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

INDIAN_PATTERNS = (
	re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}$"),  # MH12AB1234, DL3CAF0001
	re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$"),  # Bharat series 22BH1234AA
)


def normalize_vehicle_number(value: str | None) -> str:
	return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def is_valid_indian_number(value: str) -> bool:
	return any(p.match(value) for p in INDIAN_PATTERNS)


class CustomerVehicle(Document):
	def autoname(self):
		self.vehicle_number = normalize_vehicle_number(self.vehicle_number)
		self.name = self.vehicle_number

	def validate(self):
		self.vehicle_number = normalize_vehicle_number(self.vehicle_number)
		if not self.vehicle_number:
			frappe.throw(_("Vehicle Number is required"))
		if not self.is_new() and self.vehicle_number != self.name:
			frappe.throw(_("Vehicle Number cannot be edited. Use Rename if the registration really changed."))
		fmt = frappe.db.get_single_value("Car Cupid Settings", "vehicle_number_format")
		if fmt != "Free Text" and not is_valid_indian_number(self.vehicle_number):
			frappe.throw(
				_(
					"{0} does not look like an Indian registration number (e.g. MH12AB1234 or 22BH1234AA)."
				).format(frappe.bold(self.vehicle_number)),
				title=_("Check Vehicle Number"),
			)
		self.fill_contact()
		self.track_owner_change()

	def fill_contact(self):
		if self.customer and not (self.mobile_no and self.email_id):
			mobile, email = frappe.db.get_value("Customer", self.customer, ["mobile_no", "email_id"]) or (
				None,
				None,
			)
			self.mobile_no = self.mobile_no or mobile
			self.email_id = self.email_id or email

	def track_owner_change(self):
		before = self.get_doc_before_save()
		if before and before.customer != self.customer:
			self.add_comment(
				"Info",
				_("Owner changed from {0} to {1} by {2}").format(
					before.customer, self.customer, frappe.session.user
				),
			)
