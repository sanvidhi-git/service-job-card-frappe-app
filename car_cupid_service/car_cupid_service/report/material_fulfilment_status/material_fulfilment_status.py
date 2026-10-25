# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = {"creation": ("between", [f"{filters.from_date} 00:00:00", f"{filters.to_date} 23:59:59"])}
	if filters.company:
		conditions["company"] = filters.company
	if filters.status == "Pending (requested / partial)":
		conditions["status"] = ("in", ["Requested", "Partially Issued"])
	elif filters.status and filters.status != "All":
		conditions["status"] = filters.status
	if filters.job_card:
		conditions["job_card"] = filters.job_card

	rows = frappe.get_list(
		"Material Issue Request",
		filters=conditions,
		fields=[
			"name",
			"job_card",
			"job_card_item",
			"vehicle_number",
			"company",
			"item_code",
			"item_name",
			"source_warehouse",
			"requested_qty",
			"issued_qty",
			"returned_qty",
			"pending_qty",
			"status",
			"creation",
		],
		order_by="creation asc",
	)
	billed = {}
	if rows:
		billed = dict(
			frappe.get_all(
				"Service Job Card Item",
				filters={"name": ("in", [r.job_card_item for r in rows])},
				fields=["name", "billed_qty"],
				as_list=True,
			)
		)
	for r in rows:
		r["consumed_qty"] = (r.issued_qty or 0) - (r.returned_qty or 0)
		r["billed_qty"] = billed.get(r.job_card_item, 0)

	columns = [
		{
			"label": _("Request"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Material Issue Request",
			"width": 150,
		},
		{
			"label": _("Job Card"),
			"fieldname": "job_card",
			"fieldtype": "Link",
			"options": "Service Job Card",
			"width": 150,
		},
		{"label": _("Vehicle"), "fieldname": "vehicle_number", "fieldtype": "Data", "width": 110},
		{
			"label": _("Company"),
			"fieldname": "company",
			"fieldtype": "Link",
			"options": "Company",
			"width": 160,
		},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
		{
			"label": _("Store"),
			"fieldname": "source_warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 140,
		},
		{"label": _("Requested"), "fieldname": "requested_qty", "fieldtype": "Float", "width": 90},
		{"label": _("Issued"), "fieldname": "issued_qty", "fieldtype": "Float", "width": 80},
		{"label": _("Returned"), "fieldname": "returned_qty", "fieldtype": "Float", "width": 80},
		{"label": _("With Job"), "fieldname": "consumed_qty", "fieldtype": "Float", "width": 80},
		{"label": _("Billed (line)"), "fieldname": "billed_qty", "fieldtype": "Float", "width": 90},
		{"label": _("Pending"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 80},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
		{"label": _("Requested On"), "fieldname": "creation", "fieldtype": "Datetime", "width": 150},
	]
	return columns, rows
