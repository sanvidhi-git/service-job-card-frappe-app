# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	values = {"from_date": filters.from_date, "to_date": filters.to_date}
	data = frappe.db.sql(
		"""
		select b.name as batch, b.status as batch_status, b.source_doctype, b.source_name, b.customer_name,
			c.company, c.status as company_status, c.sales_invoice, c.grand_total, c.attempts, c.error,
			'Invoice' as issue
		from `tabInvoice Batch` b join `tabInvoice Batch Company` c on c.parent = b.name
		where b.posting_date between %(from_date)s and %(to_date)s and c.status in ('Failed', 'Pending')
		union all
		select b.name, b.status, b.source_doctype, b.source_name, b.customer_name,
			a.company, a.status, a.sales_invoice, a.amount, null, a.error, concat('Payment: ', a.mode_of_payment)
		from `tabInvoice Batch` b join `tabCounter Payment Allocation` a on a.parent = b.name
		where b.posting_date between %(from_date)s and %(to_date)s and a.status in ('Failed', 'Pending')
		order by batch desc
		""",
		values,
		as_dict=True,
	)
	columns = [
		{
			"label": _("Batch"),
			"fieldname": "batch",
			"fieldtype": "Link",
			"options": "Invoice Batch",
			"width": 140,
		},
		{"label": _("Batch Status"), "fieldname": "batch_status", "fieldtype": "Data", "width": 130},
		{"label": _("Issue"), "fieldname": "issue", "fieldtype": "Data", "width": 130},
		{
			"label": _("Source"),
			"fieldname": "source_name",
			"fieldtype": "Dynamic Link",
			"options": "source_doctype",
			"width": 150,
		},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 150},
		{
			"label": _("Company"),
			"fieldname": "company",
			"fieldtype": "Link",
			"options": "Company",
			"width": 160,
		},
		{"label": _("Status"), "fieldname": "company_status", "fieldtype": "Data", "width": 90},
		{
			"label": _("Invoice"),
			"fieldname": "sales_invoice",
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 140,
		},
		{"label": _("Amount"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 110},
		{"label": _("Attempts"), "fieldname": "attempts", "fieldtype": "Int", "width": 80},
		{"label": _("Error"), "fieldname": "error", "fieldtype": "Data", "width": 320},
		{"label": _("Source Type"), "fieldname": "source_doctype", "fieldtype": "Data", "hidden": 1},
	]
	return columns, data
