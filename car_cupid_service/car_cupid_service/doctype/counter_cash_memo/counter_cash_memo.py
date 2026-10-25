# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from car_cupid_service.services import line_service, source_status
from car_cupid_service.services.settings import get_settings


class CounterCashMemo(Document):
	def validate(self):
		if not self.flags.from_system:
			before = self.get_doc_before_save()
			if before and before.status in ("Billed", "Cancelled"):
				frappe.throw(_("This cash memo is {0} and can no longer be edited.").format(before.status))
			self.set_snapshot()
			line_service.validate_lines(self, "items")
			self.validate_vehicle()
		source_status.refresh_quantities(self)
		if self.flags.from_system:
			self.total_bill_discount = sum(flt(d.discount_amount) for d in self.get("bill_discounts") or [])
		else:
			line_service.validate_bill_discounts(self)
		line_service.set_totals(self)
		self.paid_amount = sum(flt(p.amount) for p in self.payments)
		source_status.set_memo_status(self)

	def on_trash(self):
		if self.invoices or any(flt(r.billed_qty) for r in self.items):
			frappe.throw(_("A billed cash memo cannot be deleted."))

	def set_snapshot(self):
		if self.customer:
			cust = frappe.db.get_value(
				"Customer", self.customer, ["customer_name", "mobile_no"], as_dict=True
			)
			if cust:
				self.customer_name = cust.customer_name
				self.mobile_no = self.mobile_no or cust.mobile_no
		if self.vehicle:
			self.vehicle_number = frappe.db.get_value("Customer Vehicle", self.vehicle, "vehicle_number")
		else:
			self.vehicle_number = None

	def validate_vehicle(self):
		if cint(get_settings().vehicle_required_for_pos) and not self.vehicle:
			frappe.throw(_("Select the customer's vehicle before billing (required by Car Cupid Settings)."))

	@frappe.whitelist()
	def cancel_memo(self, reason: str | None = None):
		self.check_permission("write")
		if any(flt(r.billed_qty) for r in self.items):
			frappe.throw(
				_("Invoiced memos cannot be cancelled here. Cancel or return the company invoices instead.")
			)
		self.status = "Cancelled"
		self.remarks = ((self.remarks or "") + "\n" + _("Cancelled: {0}").format(reason or "")).strip()
		self.flags.from_system = True
		self.save()
