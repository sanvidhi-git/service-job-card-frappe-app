// Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors

frappe.ui.form.on("Material Issue Request", {
	refresh(frm) {
		frm.disable_save();
		if (frm.doc.pending_qty > 0 && frm.doc.status !== "Cancelled") {
			frm.add_custom_button(__("Issue"), () =>
				frappe.prompt({ fieldname: "qty", fieldtype: "Float", label: __("Quantity"), default: frm.doc.pending_qty, reqd: 1 }, (v) =>
					frappe.xcall("car_cupid_service.api.inventory.record_issue", { mir: frm.doc.name, qty: v.qty }).then(() => frm.reload_doc())
				)
			).addClass("btn-primary");
		}
		if (frm.doc.issued_qty - frm.doc.returned_qty > 0) {
			frm.add_custom_button(__("Take Back"), () =>
				frappe.prompt({ fieldname: "qty", fieldtype: "Float", label: __("Quantity"), default: 1, reqd: 1 }, (v) =>
					frappe.xcall("car_cupid_service.api.inventory.record_return", { mir: frm.doc.name, qty: v.qty }).then(() => frm.reload_doc())
				)
			);
		}
		frm.add_custom_button(__("Print Issue Slip"), () => frm.print_doc());
	},
});
