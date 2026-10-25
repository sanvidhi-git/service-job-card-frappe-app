// Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors

frappe.ui.form.on("Car Cupid Settings", {
	setup(frm) {
		const by_company = (field) => (doc, cdt, cdn) => ({ filters: { company: locals[cdt][cdn].company, is_group: 0 } });
		frm.set_query("default_warehouse", "companies", by_company());
		frm.set_query("service_bay_warehouse", "companies", by_company());
		frm.set_query("cost_center", "companies", by_company());
		frm.set_query("taxes_and_charges", "companies", (doc, cdt, cdn) => ({ filters: { company: locals[cdt][cdn].company } }));
		frm.set_query("selling_price_list", "companies", () => ({ filters: { selling: 1, enabled: 1 } }));
		frm.set_query("cash_mode_of_payment", () => ({ filters: { type: "Cash", enabled: 1 } }));
		frm.set_query("default_company", () => ({ filters: { name: ["in", (frm.doc.companies || []).map((c) => c.company)] } }));
	},
	refresh(frm) {
		frm.add_custom_button(__("Open POS"), () => car_cupid.open_pos({ tab: "sale" }));
		if (!(frm.doc.companies || []).length) {
			frm.set_intro(__("Start by adding the three legal companies with their store warehouse, service bay warehouse and selling price list."), "blue");
		}
	},
});
