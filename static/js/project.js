$(function () {
    $("#key-created-modal").modal("show");

    $("[title]").tooltip();

    $(".member-remove").click(function () {
        $("#rtm-email").text(this.dataset.email);
        $("#remove-team-member-email").val(this.dataset.email);
        $("#remove-team-member-modal").modal("show");

        return false;
    });

    const projectChoices = $("#invite-team-member-modal [name=projects]");
    function updateProjectSelection() {
        const count = projectChoices.filter(":checked").length;
        $("#itm-select-all").prop("checked", count === projectChoices.length)
            .prop("indeterminate", count > 0 && count < projectChoices.length);
    }
    $("#itm-select-all").on("change", function () {
        projectChoices.prop("checked", this.checked);
        updateProjectSelection();
    });
    projectChoices.on("change", updateProjectSelection);
    updateProjectSelection();
    if ($("#invite-team-member-modal").data("open")) {
        $("#invite-team-member-modal").modal("show");
    }

    $("#invite-team-member-modal").on("shown.bs.modal", function () {
        $("#itm-email").focus();
    });

    $("#set-project-name-modal").on("shown.bs.modal", function () {
        $("#project-name").focus();
    });

    $(".add-to-team").click(function () {
        $("#itm-email").val(this.dataset.email);
        $("#itm-email-display").text(this.dataset.email);
        $("#invite-team-member-modal").modal("show");
        return false;
    });

    // Enable the submit button in transfer form when user selects
    // the target owner:
    $("#new-owner").on("change", function () {
        $("#transfer-confirm").prop("disabled", !this.value);
    });

    $("a[data-revoke-key]").click(function () {
        $("#revoke-key-type").val(this.dataset.revokeKey);
        $("#revoke-key-modal .name").text(this.dataset.name);
        $("#revoke-key-modal").modal("show");
        return false;
    });

    $("a[data-create-key]").click(function () {
        $("#create-key-type").val(this.dataset.createKey);
        $("#create-key-form").submit();
        return false;
    });

    $("code[data-plaintext]").tooltip({"title": "Click to reveal"});
    $("code[data-plaintext]").click(function () {
        $(this).text(this.dataset.plaintext).tooltip("destroy");
    });


});
