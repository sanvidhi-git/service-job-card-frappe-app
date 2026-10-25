# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime

from car_cupid_service.services import line_service, source_status
from car_cupid_service.services.settings import get_settings, is_supervisor


class ServiceJobCard(Document):
	def before_insert(self):
		self.status = "Open"

	def validate(self):
		if self.status == "Cancelled" and not self.flags.from_system:
			before = self.get_doc_before_save()
			if before and before.status == "Cancelled":
				frappe.throw(_("This job card is cancelled and can no longer be edited."))
		self.set_snapshot()
		if not self.flags.from_system:
			line_service.validate_lines(self, "items", is_job_card=True)
			self.set_line_defaults()
		source_status.refresh_quantities(self)
		line_service.set_totals(self)
		source_status.set_job_card_status(self)

	def on_update(self):
		if not self.flags.from_system and cint(get_settings().auto_create_material_request):
			from car_cupid_service.services.stock_service import request_materials

			pending = [
				r.name
				for r in self.items
				if r.is_stock_item and r.work_status != "Cancelled" and flt(r.qty) > flt(r.requested_qty)
			]
			if pending and self.status not in ("Cancelled", "Delivered"):
				request_materials(self.name, pending, silent=True)
		self.update_vehicle_history()

	def on_trash(self):
		if any(flt(r.billed_qty) or flt(r.issued_qty) for r in self.items):
			frappe.throw(_("A job card with issued or billed lines cannot be deleted. Cancel it instead."))

	# ------------------------------------------------------------------ helpers
	def set_snapshot(self):
		"""Freeze customer / vehicle details at intake; later master edits do not rewrite history."""
		before = self.get_doc_before_save()
		customer_changed = not before or before.customer != self.customer
		vehicle_changed = not before or before.vehicle != self.vehicle
		if any(flt(r.billed_qty) for r in self.items) and before and (customer_changed or vehicle_changed):
			frappe.throw(_("Customer and vehicle cannot be changed after billing has started."))
		if customer_changed or not self.customer_name:
			cust = frappe.db.get_value(
				"Customer", self.customer, ["customer_name", "mobile_no", "email_id"], as_dict=True
			)
			if cust:
				self.customer_name = cust.customer_name
				self.mobile_no = (
					self.mobile_no
					if (before and not customer_changed)
					else (self.mobile_no or cust.mobile_no)
				)
				self.email_id = self.email_id or cust.email_id
		if self.vehicle and (vehicle_changed or not self.vehicle_number):
			v = frappe.db.get_value(
				"Customer Vehicle",
				self.vehicle,
				["vehicle_number", "make", "model", "variant", "colour", "customer"],
				as_dict=True,
			)
			if v:
				if v.customer != self.customer:
					frappe.msgprint(
						_("Vehicle {0} is registered to another customer ({1}).").format(
							v.vehicle_number, v.customer
						),
						indicator="orange",
						alert=True,
					)
				self.vehicle_number = v.vehicle_number
				self.vehicle_description = " ".join(filter(None, [v.make, v.model, v.variant, v.colour]))

	def set_line_defaults(self):
		for row in self.items:
			if (
				not row.technician
				and self.default_technician
				and row.item_type in ("Service", "Labour", "Package")
			):
				row.technician = self.default_technician
			if not row.is_stock_item:
				row.requested_qty = row.issued_qty = row.returned_qty = 0

	def update_vehicle_history(self):
		if not self.vehicle:
			return
		vals = {"last_job_card": self.name, "last_service_date": self.posting_datetime}
		if cint(self.odometer):
			last = cint(frappe.db.get_value("Customer Vehicle", self.vehicle, "last_odometer"))
			if cint(self.odometer) >= last:
				vals["last_odometer"] = cint(self.odometer)
		frappe.db.set_value("Customer Vehicle", self.vehicle, vals, update_modified=False)

	# ------------------------------------------------------------------ whitelisted actions
	@frappe.whitelist()
	def set_work_status(self, rows: list | str | None = None, status: str = "Completed"):
		self.check_permission("write")
		if status not in ("Pending", "In Progress", "Completed", "Cancelled"):
			frappe.throw(_("Invalid status"))
		rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
		for row in self.items:
			if rows and row.name not in rows:
				continue
			if status == "Cancelled":
				if flt(row.billed_qty):
					frappe.throw(_("Row {0} is already invoiced and cannot be cancelled").format(row.idx))
				if flt(row.issued_qty) - flt(row.returned_qty) > 0:
					frappe.throw(
						_("Row {0}: return the issued material to stores before cancelling").format(row.idx)
					)
			if row.work_status != "Cancelled" or status != "Completed":
				row.work_status = status
		self.save()
		return self.status

	@frappe.whitelist()
	def mark_delivered(self):
		self.check_permission("write")
		if self.billing_status != "Fully Billed" and not is_supervisor():
			frappe.throw(_("Bill all lines before delivering the vehicle (or ask a Billing Supervisor)."))
		self.delivery_status = "Delivered"
		self.delivered_on = now_datetime()
		self.flags.from_system = True
		self.save()
		return self.status

	@frappe.whitelist()
	def cancel_job(self, reason: str):
		self.check_permission("write")
		if not reason:
			frappe.throw(_("Please give a reason for cancelling"))
		if any(flt(r.billed_qty) for r in self.items):
			frappe.throw(_("Invoiced job cards cannot be cancelled. Cancel or return the invoices first."))
		if any(flt(r.issued_qty) - flt(r.returned_qty) > 0 for r in self.items):
			frappe.throw(_("Return all issued material to stores before cancelling the job."))
		for mir in frappe.get_all(
			"Material Issue Request", filters={"job_card": self.name, "status": "Requested"}, pluck="name"
		):
			from car_cupid_service.services.stock_service import cancel_request

			cancel_request(mir)
		self.reload()
		self.status = "Cancelled"
		self.cancellation_reason = reason
		self.flags.from_system = True
		self.save()
		return self.status
