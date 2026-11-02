$(function () {
    const modal = $("#maintenance-modal");
    function updateFields() {
        const allProjects = modal.find("[name=all_projects]").prop("checked") === true;
        modal.find("[name=kind] option[value=weekly]").prop("disabled", allProjects);
        if (allProjects) {
            modal.find("[name=kind]").val("one_time");
        }
        modal.find(".maintenance-project-scope").toggle(!allProjects).find("input").prop("disabled", allProjects);
        const recurring = modal.find("[name=kind]").val() === "weekly";
        modal.find(".maintenance-dates").toggle(!recurring).find("input").prop("disabled", recurring);
        modal.find(".maintenance-weekdays, .maintenance-recurring-help").toggle(recurring);
        modal.find(".maintenance-weekdays input").prop("disabled", !recurring);
        const allServices = modal.find("[name=all_checks]").prop("checked");
        modal.find(".maintenance-services").toggle(!allProjects && !allServices).find("input").prop("disabled", allProjects || allServices);
    }
    $(document).on("click", "[data-maintenance-url]", function (event) {
        event.preventDefault();
        $.get($(this).data("maintenance-url"))
            .done(function (html) {
                modal.html(html).modal("show");
                updateFields();
            })
            .fail(function () {
                modal.html('<div class="modal-dialog"><div class="modal-content"><div class="modal-body"><p>Could not load the maintenance window. Please try again.</p><button class="btn btn-default" data-dismiss="modal">Close</button></div></div></div>').modal("show");
            });
    });
    modal.on("change", "[name=kind], [name=all_checks], [name=all_projects]", updateFields);
    modal.on("submit", ".maintenance-form", function (event) {
        event.preventDefault();
        const form = $(this);
        form.find("[type=submit]").prop("disabled", true);
        $.ajax({url: form.attr("action"), method: "POST", data: form.serialize()})
            .done(function (data) { window.location.assign(data.redirect); })
            .fail(function (response) {
                if (response.status === 400) {
                    modal.html(response.responseText);
                    updateFields();
                } else {
                    modal.find(".maintenance-error").text("Could not save maintenance. Your changes have not been saved; please try again.").show();
                }
                modal.find("[type=submit]").prop("disabled", false);
            });
    });
    $("[data-maintenance-delete]").on("click", function () {
        const target = $("#maintenance-delete-modal");
        target.find("[name=window]").val($(this).data("maintenance-delete"));
        target.find(".maintenance-delete-name").text($(this).data("maintenance-name"));
        target.modal("show");
    });
    if (modal.data("open")) {
        modal.modal("show");
        updateFields();
    }
});
