# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class ServiceIncentiveRule(Document):
	def validate(self):
		if flt(self.rate) < 0:
			frappe.throw(_("Rate cannot be negative"))
		if self.basis == "Percentage of Net Sales" and flt(self.rate) > 100:
			frappe.throw(_("A percentage incentive cannot exceed 100%"))
		if self.valid_from and self.valid_upto and getdate(self.valid_from) > getdate(self.valid_upto):
			frappe.throw(_("Valid From cannot be after Valid Upto"))
		if self.item_code and self.item_group:
			frappe.throw(_("Set either an Item or an Item Group, not both"))
