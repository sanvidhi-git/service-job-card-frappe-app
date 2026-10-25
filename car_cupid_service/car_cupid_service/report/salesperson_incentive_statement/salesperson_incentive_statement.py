# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

from car_cupid_service.services.incentive_service import estimate_for_line


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = {"posting_date": ("between", [filters.from_date, filters.to_date])}
	if filters.sales_person:
		conditions["sales_person"] = filters.sales_person
	if filters.company:
		conditions["company"] = filters.company
	entries = frappe.get_list(
		"Service Incentive Ledger",
		filters=conditions,
		fields=[
			"sales_person",
			"company",
			"posting_date",
			"entry_type",
			"sales_invoice",
			"against_invoice",
			"item_code",
			"item_group",
			"eligible_net_sales",
			"incentive_rule",
			"basis",
			"rate",
			"incentive_amount",
		],
		order_by="sales_person, posting_date",
	)
	data = []
	for e in entries:
		e["earned"] = flt(e.incentive_amount) if e.entry_type == "Earned" else 0
		e["reversal"] = flt(e.incentive_amount) if e.entry_type == "Reversal" else 0
		e["net"] = flt(e.incentive_amount)
		e["kind"] = e.entry_type
		data.append(e)

	if filters.include_estimated:
		for doctype, child in (
			("Service Job Card", "Service Job Card Item"),
			("Counter Cash Memo", "Counter Cash Memo Item"),
		):
			parents = frappe.get_list(
				doctype,
				filters={"status": ("not in", ["Billed", "Delivered", "Cancelled"])},
				pluck="name",
				limit_page_length=1000,
			)
			if not parents:
				continue
			for row in frappe.get_all(
				child,
				filters={"parent": ("in", parents)},
				fields=[
					"parent",
					"item_code",
					"item_type",
					"company",
					"sales_person",
					"qty",
					"billed_qty",
					"rate",
				],
			):
				if filters.sales_person and row.sales_person != filters.sales_person:
					continue
				if filters.company and row.company != filters.company:
					continue
				amount = estimate_for_line(row, row.company)
				if not amount:
					continue
				data.append(
					{
						"sales_person": row.sales_person,
						"company": row.company,
						"kind": _("Estimated ({0})").format(row.parent),
						"item_code": row.item_code,
						"eligible_net_sales": flt(row.rate) * (flt(row.qty) - flt(row.billed_qty)),
						"estimated": amount,
					}
				)

	columns = [
		{
			"label": _("Salesperson"),
			"fieldname": "sales_person",
			"fieldtype": "Link",
			"options": "Sales Person",
			"width": 160,
		},
		{
			"label": _("Company"),
			"fieldname": "company",
			"fieldtype": "Link",
			"options": "Company",
			"width": 160,
		},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Type"), "fieldname": "kind", "fieldtype": "Data", "width": 140},
		{
			"label": _("Invoice"),
			"fieldname": "sales_invoice",
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 150,
		},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{
			"label": _("Item Group"),
			"fieldname": "item_group",
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 120,
		},
		{
			"label": _("Eligible Net Sales"),
			"fieldname": "eligible_net_sales",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Rule"),
			"fieldname": "incentive_rule",
			"fieldtype": "Link",
			"options": "Service Incentive Rule",
			"width": 140,
		},
		{"label": _("Rate"), "fieldname": "rate", "fieldtype": "Float", "width": 70},
		{"label": _("Earned"), "fieldname": "earned", "fieldtype": "Currency", "width": 110},
		{"label": _("Reversal"), "fieldname": "reversal", "fieldtype": "Currency", "width": 110},
		{"label": _("Net Incentive"), "fieldname": "net", "fieldtype": "Currency", "width": 120},
		{"label": _("Estimated (unbilled)"), "fieldname": "estimated", "fieldtype": "Currency", "width": 140},
	]
	summary = [
		{
			"label": _("Earned"),
			"value": sum(flt(d.get("earned")) for d in data),
			"datatype": "Currency",
			"indicator": "green",
		},
		{
			"label": _("Reversals"),
			"value": sum(flt(d.get("reversal")) for d in data),
			"datatype": "Currency",
			"indicator": "red",
		},
		{
			"label": _("Net Payable"),
			"value": sum(flt(d.get("net")) for d in data),
			"datatype": "Currency",
			"indicator": "blue",
		},
		{
			"label": _("Estimated"),
			"value": sum(flt(d.get("estimated")) for d in data),
			"datatype": "Currency",
			"indicator": "gray",
		},
	]
	return columns, data, None, None, summary
