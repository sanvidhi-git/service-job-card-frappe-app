# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = {
		"opening_time": ("between", [f"{filters.from_date} 00:00:00", f"{filters.to_date} 23:59:59"])
	}
	if filters.cashier:
		conditions["cashier"] = filters.cashier
	if filters.variance_only:
		conditions["variance"] = ("!=", 0)
	data = frappe.get_list(
		"Counter Session",
		filters=conditions,
		fields=[
			"name",
			"counter_name",
			"cashier",
			"status",
			"opening_time",
			"closing_time",
			"opening_float",
			"cash_receipts",
			"non_cash_receipts",
			"cash_in",
			"cash_out",
			"expected_cash",
			"physical_cash",
			"variance",
			"variance_reason",
			"variance_approved_by",
		],
		order_by="opening_time desc",
	)
	columns = [
		{
			"label": _("Session"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Counter Session",
			"width": 140,
		},
		{"label": _("Counter"), "fieldname": "counter_name", "fieldtype": "Data", "width": 110},
		{"label": _("Cashier"), "fieldname": "cashier", "fieldtype": "Link", "options": "User", "width": 160},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		{"label": _("Opened"), "fieldname": "opening_time", "fieldtype": "Datetime", "width": 150},
		{"label": _("Closed"), "fieldname": "closing_time", "fieldtype": "Datetime", "width": 150},
		{"label": _("Float"), "fieldname": "opening_float", "fieldtype": "Currency", "width": 100},
		{"label": _("Cash Receipts"), "fieldname": "cash_receipts", "fieldtype": "Currency", "width": 120},
		{
			"label": _("UPI / Card / Bank"),
			"fieldname": "non_cash_receipts",
			"fieldtype": "Currency",
			"width": 130,
		},
		{"label": _("Cash In"), "fieldname": "cash_in", "fieldtype": "Currency", "width": 100},
		{"label": _("Cash Out"), "fieldname": "cash_out", "fieldtype": "Currency", "width": 100},
		{"label": _("Expected Cash"), "fieldname": "expected_cash", "fieldtype": "Currency", "width": 120},
		{"label": _("Counted"), "fieldname": "physical_cash", "fieldtype": "Currency", "width": 110},
		{"label": _("Variance"), "fieldname": "variance", "fieldtype": "Currency", "width": 110},
		{"label": _("Reason"), "fieldname": "variance_reason", "fieldtype": "Data", "width": 180},
		{
			"label": _("Approved By"),
			"fieldname": "variance_approved_by",
			"fieldtype": "Link",
			"options": "User",
			"width": 150,
		},
	]
	summary = [
		{"label": _("Sessions"), "value": len(data), "indicator": "blue"},
		{
			"label": _("Net Variance"),
			"value": sum(flt(d.variance) for d in data),
			"datatype": "Currency",
			"indicator": "red",
		},
	]
	return columns, data, None, None, summary
