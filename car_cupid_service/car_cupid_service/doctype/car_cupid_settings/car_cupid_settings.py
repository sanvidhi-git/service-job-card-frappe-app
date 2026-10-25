# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class CarCupidSettings(Document):
	def validate(self):
		seen = set()
		errors = []
		enabled = []
		for row in self.companies:
			if row.company in seen:
				errors.append(_("Row {0}: company {1} is listed twice").format(row.idx, row.company))
			seen.add(row.company)
			if cint(row.enabled):
				enabled.append(row.company)
			for field in ("default_warehouse", "service_bay_warehouse"):
				wh = row.get(field)
				if wh and frappe.db.get_value("Warehouse", wh, "company") != row.company:
					errors.append(
						_("Row {0}: {1} {2} does not belong to {3}").format(
							row.idx, row.meta.get_label(field), wh, row.company
						)
					)
			if row.default_warehouse and row.default_warehouse == row.service_bay_warehouse:
				errors.append(
					_("Row {0}: Store and Service Bay warehouses must be different").format(row.idx)
				)
			if (
				self.material_issue_policy != "Not Required"
				and not row.service_bay_warehouse
				and cint(row.enabled)
			):
				errors.append(
					_(
						"Row {0}: set a Service Bay (WIP) Warehouse for {1}, or set Material Issue Policy to 'Not Required'"
					).format(row.idx, row.company)
				)
			if (
				row.cost_center
				and frappe.db.get_value("Cost Center", row.cost_center, "company") != row.company
			):
				errors.append(
					_("Row {0}: cost center {1} does not belong to {2}").format(
						row.idx, row.cost_center, row.company
					)
				)
			if (
				row.taxes_and_charges
				and frappe.db.get_value("Sales Taxes and Charges Template", row.taxes_and_charges, "company")
				!= row.company
			):
				errors.append(
					_("Row {0}: tax template {1} does not belong to {2}").format(
						row.idx, row.taxes_and_charges, row.company
					)
				)
			if not row.short_label:
				row.short_label = (frappe.db.get_value("Company", row.company, "abbr") or row.company)[:16]

		if self.default_company and self.default_company not in enabled:
			errors.append(_("Default Company must be one of the enabled companies"))
		if not self.default_company and enabled:
			self.default_company = enabled[0]
		if self.max_discount_without_approval and not (0 <= self.max_discount_without_approval <= 100):
			errors.append(_("Max discount must be between 0 and 100"))
		if (
			self.cash_mode_of_payment
			and frappe.db.get_value("Mode of Payment", self.cash_mode_of_payment, "type") != "Cash"
		):
			errors.append(_("{0} is not a Cash type Mode of Payment").format(self.cash_mode_of_payment))
		if errors:
			frappe.throw("<br>".join(errors), title=_("Invalid Settings"))

	def on_update(self):
		frappe.clear_cache(doctype="Car Cupid Settings")
		from car_cupid_service.services.access import sync_company_access

		sync_company_access()
