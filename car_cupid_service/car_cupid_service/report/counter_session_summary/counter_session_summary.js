frappe.query_reports["Counter Session Summary"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -7), reqd: 1 },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "cashier", label: __("Cashier"), fieldtype: "Link", options: "User" },
		{ fieldname: "variance_only", label: __("With Variance Only"), fieldtype: "Check" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "variance" && data && data.variance) value = `<span style="color:var(--red-600);font-weight:600">${value}</span>`;
		return value;
	},
};
