frappe.query_reports["Company Wise Counter Sales"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Company", "Item", "Item Group", "Salesperson", "Line Type", "Date", "Mode of Payment", "Source"],
			default: "Company",
		},
		{ fieldname: "source", label: __("Source"), fieldtype: "Select", options: ["", "Service Job Card", "Counter Cash Memo"] },
	],
};
