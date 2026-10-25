# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class MaterialIssueRequest(Document):
	def validate(self):
		self.set_quantities()

	def set_quantities(self):
		"""Issued / returned quantities come only from submitted Stock Entries in the movement log."""
		issued = returned = 0.0
		for m in self.movements:
			if not m.stock_entry or frappe.db.get_value("Stock Entry", m.stock_entry, "docstatus") != 1:
				continue
			if m.movement_type == "Issue":
				issued += flt(m.qty)
			else:
				returned += flt(m.qty)
		self.issued_qty = issued
		self.returned_qty = returned
		if self.status == "Cancelled":
			self.pending_qty = 0
			return
		self.pending_qty = max(flt(self.requested_qty) - issued, 0)
		if issued <= 0:
			self.status = "Requested"
		elif returned >= issued:
			self.status = "Returned"
		elif self.pending_qty > 0:
			self.status = "Partially Issued"
		else:
			self.status = "Issued"
