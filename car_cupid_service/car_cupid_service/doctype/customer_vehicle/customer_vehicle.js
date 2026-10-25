// Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors

frappe.ui.form.on("Customer Vehicle", {
	vehicle_number(frm) {
		const v = (frm.doc.vehicle_number || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
		if (v !== frm.doc.vehicle_number) frm.set_value("vehicle_number", v);
	},
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("New Job Card"), () => frappe.new_doc("Service Job Card", { customer: frm.doc.customer, vehicle: frm.doc.name }));
		frm.add_custom_button(__("Service History"), () => frappe.set_route("List", "Service Job Card", { vehicle: frm.doc.name }));
	},
});
