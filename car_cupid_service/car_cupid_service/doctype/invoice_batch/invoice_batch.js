// Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors

frappe.ui.form.on("Invoice Batch", {
	refresh(frm) {
		frm.disable_save();
		const color = { Submitted: "green", "Partially Submitted": "orange", Failed: "red", "In Progress": "blue", Cancelled: "gray" }[frm.doc.status];
		frm.page.set_indicator(__(frm.doc.status), color || "gray");
		frm.add_custom_button(__("Print Customer Memo"), () =>
			window.open(frappe.urllib.get_full_url(`/printview?doctype=Invoice%20Batch&name=${encodeURIComponent(frm.doc.name)}&format=${encodeURIComponent("Car Cupid Consolidated Memo")}&trigger_print=1`))
		);
		const failed_pay = (frm.doc.allocations || []).some((a) => ["Failed", "Pending"].includes(a.status));
		if (["Failed", "Partially Submitted", "In Progress"].includes(frm.doc.status) || failed_pay) {
			frm.add_custom_button(__("Retry Failed Steps"), () =>
				frm.call("retry").then((r) => {
					frappe.show_alert({ message: __("Batch is now {0}", [r.message.status]), indicator: r.message.status === "Submitted" ? "green" : "orange" });
					frm.reload_doc();
				})
			).addClass("btn-primary");
			frm.set_intro(__("Retry re-creates only the failed company invoices and unposted payments. Submitted invoices are never duplicated."), "orange");
		}
		if (frm.doc.source_name) {
			frm.add_custom_button(__("Open {0}", [__(frm.doc.source_doctype)]), () => frappe.set_route("Form", frm.doc.source_doctype, frm.doc.source_name));
		}
	},
});
