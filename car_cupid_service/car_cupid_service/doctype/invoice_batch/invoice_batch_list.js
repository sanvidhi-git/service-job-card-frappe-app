frappe.listview_settings["Invoice Batch"] = {
	get_indicator(doc) {
		const map = { Submitted: "green", "Partially Submitted": "orange", Failed: "red", "In Progress": "blue", Cancelled: "gray" };
		return [__(doc.status), map[doc.status] || "gray", "status,=," + doc.status];
	},
};
