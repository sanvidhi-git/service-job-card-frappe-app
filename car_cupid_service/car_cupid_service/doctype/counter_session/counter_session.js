// Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors

frappe.ui.form.on("Counter Denomination", {
	count(frm, cdt, cdn) {
		const r = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "amount", flt(r.denomination) * cint(r.count));
		const total = (frm.doc.closing_denominations || []).reduce((a, d) => a + flt(d.denomination) * cint(d.count), 0);
		frm.set_value("physical_cash", total);
		frm.set_value("variance", total - flt(frm.doc.expected_cash));
	},
});

frappe.ui.form.on("Counter Session", {
	refresh(frm) {
		const color = { Open: "green", "Pending Review": "orange", Closed: "gray", Cancelled: "red" }[frm.doc.status];
		frm.page.set_indicator(__(frm.doc.status), color || "gray");
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Count & Close in POS"), () => car_cupid.open_pos({ tab: "cash" }));
			if (frm.doc.status === "Pending Review") {
				frm.set_intro(__("Cash difference of {0} is waiting for a supervisor. Tick 'Variance Approved' and Submit to close.", [format_currency(frm.doc.variance)]), "orange");
			}
		}
	},
});
