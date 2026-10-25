frappe.query_reports["Invoice Batch Exceptions"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -30), reqd: 1 },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
	],
	onload(report) {
		report.page.add_inner_button(__("Open POS Recovery"), () => {
			frappe.route_options = { tab: "recovery" };
			frappe.set_route("car-cupid-pos");
		});
	},
};
