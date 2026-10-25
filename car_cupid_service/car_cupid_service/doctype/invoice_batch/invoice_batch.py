# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class InvoiceBatch(Document):
	def on_trash(self):
		if any(r.sales_invoice for r in self.companies):
			frappe.throw(_("An invoice batch that created invoices cannot be deleted; it is part of the audit trail."))

	@frappe.whitelist()
	def retry(self):
		from car_cupid_service.services.billing_service import retry_batch

		return retry_batch(self.name)
