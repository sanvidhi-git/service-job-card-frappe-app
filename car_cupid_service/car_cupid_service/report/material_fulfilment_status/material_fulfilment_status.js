frappe.query_reports["Material Fulfilment Status"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -30), reqd: 1 },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["Pending (requested / partial)", "All", "Requested", "Partially Issued", "Issued", "Returned", "Cancelled"],
			default: "Pending (requested / partial)",
		},
		{ fieldname: "job_card", label: __("Job Card"), fieldtype: "Link", options: "Service Job Card" },
	],
};
