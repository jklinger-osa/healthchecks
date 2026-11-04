$(function () {
    var base = document.getElementById("base-url").getAttribute("href").slice(0, -1);
    var favicon = document.querySelector('link[rel="icon"]');
    var filter = document.getElementById("only-problem-projects");
    var table = $("#checks-table");
    var body = table.find("tbody");
    var storageKey = filter.dataset.storageKey;
    try {
        filter.checked = localStorage.getItem(storageKey) === "true";
    } catch (error) {
        filter.checked = false;
    }

    function applyProjectFilter() {
        var visibleProblems = 0;
        $("#project-selector .project-card").each(function () {
            var problem = this.dataset.status === "down" || this.dataset.status === "grace";
            if (problem) visibleProblems++;
            $(this).toggle(!filter.checked || problem);
        });
        $("#no-problem-projects").toggle(filter.checked && visibleProblems === 0);
    }
    $(filter).on("change", function () {
        try {
            localStorage.setItem(storageKey, String(this.checked));
        } catch (error) {
            // The filter still works when browser storage is unavailable.
        }
        applyProjectFilter();
    });
    applyProjectFilter();

    function updateProblemServices(rows) {
        var current = new Set();
        var previous = body.children("tr").not(".checks-row").first();
        rows.forEach(function (item) {
            current.add(item.code);
            var row = table.find("tr.checks-row").filter(function () { return this.id === item.code; });
            if (!row.length || row.hasClass("rw") !== item.editable) {
                if (row.length) {
                    row.find("[data-toggle=tooltip], .pause, .last-ping, .my-checks-url, .integrations, .integrations span").tooltip("destroy");
                    row.remove();
                }
                row = $(item.html.trim());
                body.append(row);
                window.initializeCheckTooltips(row);
            } else {
                row.find(".status").attr("class", "status ic-" + item.status);
                row.find(".spinner").toggleClass("started", item.started);
                row.find(".last-ping").html(item.last_ping);
                row.find(".maintenance-indicator-slot").html(item.maintenance);
            }
            if (previous.next()[0] !== row[0]) row.insertAfter(previous);
            previous = row;
        });
        table.find("tr.checks-row").each(function () {
            if (!current.has(this.id)) {
                $(this).find("[data-toggle=tooltip], .pause, .last-ping, .my-checks-url, .integrations, .integrations span").tooltip("destroy");
                $(this).remove();
            }
        });
        table.toggle(rows.length > 0);
        $("#no-problem-services").toggle(rows.length === 0);
    }

    function refreshStatus() {
        $.ajax({
            url: base + "?refresh=1&problems=1",
            dataType: "json",
            timeout: 5000,
            success: function (data) {
                var anyDown = false;
                $("#project-selector .project-card").each(function () {
                    var status = data.projects[this.dataset.projectCode];
                    if (!status) {
                        $(this).remove();
                        return;
                    }
                    this.dataset.status = status.status;
                    anyDown = anyDown || status.status === "down";
                    $(this).find("div.status").attr("class", "status ic-" + status.status);
                    $(this).find("div.spinner").toggleClass("started", status.started);
                });
                applyProjectFilter();
                updateProblemServices(data.rows);
                favicon.href = base + "/static/img/favicon" + (anyDown ? "_down" : "") + ".svg";
            }
        });
    }
    adaptiveSetInterval(refreshStatus);
});
