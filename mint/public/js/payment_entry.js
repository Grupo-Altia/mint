console.log("Loading custom Payment Entry JS from mint");
frappe.ui.form.on("Payment Entry", {
	refresh: function (frm) {
		setTimeout(() => {
			// Remover badge personalizado si ya existe
			frm.page.wrapper.find('.custom-recon-badge').remove();

			if (frm.doc.docstatus === 0 || frm.doc.docstatus === 1) {
				let label, color;
				
				if (frm.doc.docstatus === 0) {
					label = "Por conciliar";
					color = "orange";
				} else {
					let status = frm.doc.custom_reconciliation_status;
					label = status === "Conciliado" ? "Conciliado" : "Por conciliar";
					color = status === "Conciliado" ? "green" : "orange";
				}
				
				let custom_badge = $(`<span class="indicator-pill whitespace-nowrap ${color} custom-recon-badge" style="margin-left: 8px;">${__(label)}</span>`);
				
				// Seleccionar el contenedor donde está el título y el badge estándar
				let title_container = frm.page.wrapper.find('.title-text').parent();
				if (title_container.length) {
					title_container.append(custom_badge);
				}
			}
		}, 600);
	}
});
