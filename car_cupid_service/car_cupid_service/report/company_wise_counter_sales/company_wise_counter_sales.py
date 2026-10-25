# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _

GROUPS = {
	"Company": ("si.company", _("Company"), "Link", "Company"),
	"Item": ("sii.item_code", _("Item"), "Link", "Item"),
	"Item Group": ("sii.item_group", _("Item Group"), "Link", "Item Group"),
	"Salesperson": ("sii.cc_sales_person", _("Salesperson"), "Link", "Sales Person"),
	"Line Type": ("sii.cc_item_type", _("Line Type"), "Data", None),
	"Date": ("si.posting_date", _("Date"), "Date", None),
	"Source": ("si.cc_source_doctype", _("Source"), "Data", None),
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if filters.group_by == "Mode of Payment":
		return by_mode(filters)
	expr, label, ftype, options = GROUPS.get(filters.group_by or "Company", GROUPS["Company"])
	cond = ""
	values = {"from_date": filters.from_date, "to_date": filters.to_date}
	if filters.company:
		cond += " and si.company = %(company)s"
		values["company"] = filters.company
	if filters.source:
		cond += " and si.cc_source_doctype = %(source)s"
		values["source"] = filters.source
	data = frappe.db.sql(
		f"""
		select {expr} as grp, si.company as company,
			count(distinct si.name) as invoices,
			sum(sii.stock_qty) as qty,
			sum(sii.base_net_amount) as net_amount
		from `tabSales Invoice Item` sii
		join `tabSales Invoice` si on si.name = sii.parent
		where si.docstatus = 1 and ifnull(si.cc_source_doctype, '') != ''
			and si.posting_date between %(from_date)s and %(to_date)s {cond}
		group by {expr}, si.company
		order by si.company, net_amount desc
		""",
		values,
		as_dict=True,
	)
	tax = dict(
		frappe.db.sql(
			f"""
			select si.company, sum(si.base_total_taxes_and_charges)
			from `tabSales Invoice` si
			where si.docstatus = 1 and ifnull(si.cc_source_doctype, '') != ''
				and si.posting_date between %(from_date)s and %(to_date)s {cond}
			group by si.company
			""",
			values,
		)
	)
	columns = [
		{"label": label, "fieldname": "grp", "fieldtype": ftype, "options": options, "width": 200},
		{
			"label": _("Company"),
			"fieldname": "company",
			"fieldtype": "Link",
			"options": "Company",
			"width": 180,
		},
		{"label": _("Invoices"), "fieldname": "invoices", "fieldtype": "Int", "width": 90},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 90},
		{
			"label": _("Net Sales (excl. tax)"),
			"fieldname": "net_amount",
			"fieldtype": "Currency",
			"width": 160,
		},
	]
	if (filters.group_by or "Company") == "Company":
		columns.append({"label": _("Tax"), "fieldname": "tax", "fieldtype": "Currency", "width": 130})
		for d in data:
			d["tax"] = tax.get(d.company, 0)
	chart = {
		"data": {
			"labels": [str(d.grp) for d in data[:12]],
			"datasets": [{"name": _("Net Sales"), "values": [d.net_amount for d in data[:12]]}],
		},
		"type": "bar",
	}
	return columns, data, None, chart


def by_mode(filters):
	values = {"from_date": filters.from_date, "to_date": filters.to_date}
	cond = ""
	if filters.company:
		cond = " and company = %(company)s"
		values["company"] = filters.company
	data = frappe.db.sql(
		f"""
		select mode_of_payment as grp, company, count(*) as invoices, sum(paid_amount) as net_amount
		from `tabPayment Entry`
		where docstatus = 1 and ifnull(cc_invoice_batch, '') != ''
			and posting_date between %(from_date)s and %(to_date)s {cond}
		group by mode_of_payment, company
		order by company, net_amount desc
		""",
		values,
		as_dict=True,
	)
	columns = [
		{
			"label": _("Mode of Payment"),
			"fieldname": "grp",
			"fieldtype": "Link",
			"options": "Mode of Payment",
			"width": 180,
		},
		{
			"label": _("Company"),
			"fieldname": "company",
			"fieldtype": "Link",
			"options": "Company",
			"width": 180,
		},
		{"label": _("Receipts"), "fieldname": "invoices", "fieldtype": "Int", "width": 90},
		{"label": _("Amount"), "fieldname": "net_amount", "fieldtype": "Currency", "width": 150},
	]
	return columns, data
