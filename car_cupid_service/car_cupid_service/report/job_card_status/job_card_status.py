# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime, time_diff_in_hours


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = {
		"posting_datetime": ("between", [f"{filters.from_date} 00:00:00", f"{filters.to_date} 23:59:59"])
	}
	if filters.status == "Open (not billed)":
		conditions["status"] = ("in", ["Open", "Work In Progress", "Ready for Billing", "Partially Billed"])
	elif filters.status and filters.status != "All":
		conditions["status"] = filters.status
	if filters.customer:
		conditions["customer"] = filters.customer

	rows = frappe.get_list(
		"Service Job Card",
		filters=conditions,
		fields=[
			"name",
			"posting_datetime",
			"vehicle_number",
			"customer_name",
			"status",
			"work_status",
			"fulfilment_status",
			"billing_status",
			"priority",
			"promised_delivery",
			"net_total",
			"total_billed_amount",
		],
		order_by="posting_datetime asc",
	)
	now = now_datetime()
	data = []
	for r in rows:
		open_job = r.status not in ("Billed", "Delivered", "Cancelled")
		r["age_hours"] = round(time_diff_in_hours(now, r.posting_datetime), 1) if open_job else None
		r["overdue"] = (
			1 if (open_job and r.promised_delivery and get_datetime(r.promised_delivery) < now) else 0
		)
		if filters.overdue_only and not r["overdue"]:
			continue
		data.append(r)

	columns = [
		{
			"label": _("Job Card"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Service Job Card",
			"width": 150,
		},
		{"label": _("Opened"), "fieldname": "posting_datetime", "fieldtype": "Datetime", "width": 150},
		{"label": _("Vehicle"), "fieldname": "vehicle_number", "fieldtype": "Data", "width": 120},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 160},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 130},
		{"label": _("Work"), "fieldname": "work_status", "fieldtype": "Data", "width": 100},
		{"label": _("Stores"), "fieldname": "fulfilment_status", "fieldtype": "Data", "width": 120},
		{"label": _("Billing"), "fieldname": "billing_status", "fieldtype": "Data", "width": 120},
		{"label": _("Priority"), "fieldname": "priority", "fieldtype": "Data", "width": 80},
		{"label": _("Promised"), "fieldname": "promised_delivery", "fieldtype": "Datetime", "width": 150},
		{"label": _("Overdue"), "fieldname": "overdue", "fieldtype": "Check", "width": 80},
		{
			"label": _("Age (hrs)"),
			"fieldname": "age_hours",
			"fieldtype": "Float",
			"precision": 1,
			"width": 90,
		},
		{"label": _("Net Total"), "fieldname": "net_total", "fieldtype": "Currency", "width": 120},
		{"label": _("Invoiced"), "fieldname": "total_billed_amount", "fieldtype": "Currency", "width": 120},
	]
	summary = [
		{"label": _("Jobs"), "value": len(data), "indicator": "blue"},
		{"label": _("Overdue"), "value": sum(r["overdue"] for r in data), "indicator": "red"},
		{
			"label": _("Ready for Billing"),
			"value": len([r for r in data if r.status in ("Ready for Billing", "Partially Billed")]),
			"indicator": "green",
		},
	]
	return columns, data, None, None, summary
