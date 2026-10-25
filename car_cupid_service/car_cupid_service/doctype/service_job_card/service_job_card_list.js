frappe.listview_settings["Service Job Card"] = {
	add_fields: ["status", "promised_delivery", "billing_status"],
	get_indicator(doc) {
		const map = { Open: "blue", "Work In Progress": "orange", "Ready for Billing": "green", "Partially Billed": "orange", Billed: "green", Delivered: "gray", Cancelled: "red" };
		return [__(doc.status), map[doc.status] || "gray", "status,=," + doc.status];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Open POS"), () => car_cupid.open_pos({ tab: "jobs" }));
	},
};
