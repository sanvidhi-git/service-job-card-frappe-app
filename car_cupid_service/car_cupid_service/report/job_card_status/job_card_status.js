frappe.query_reports["Job Card Status"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -30), reqd: 1 },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["Open (not billed)", "All", "Open", "Work In Progress", "Ready for Billing", "Partially Billed", "Billed", "Delivered", "Cancelled"],
			default: "Open (not billed)",
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "overdue_only", label: __("Overdue Only"), fieldtype: "Check" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "overdue" && data && data.overdue) value = `<span class="indicator-pill red">${__("Overdue")}</span>`;
		return value;
	},
};
