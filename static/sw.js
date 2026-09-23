self.addEventListener("push", event => {
    let data = {
        title: "ACT Doctor Call",
        body: "A new case is waiting for review.",
        url: "/doctor"
    };

    if (event.data) {
        try {
            data = event.data.json();
        } catch (e) {
            data.body = event.data.text();
        }
    }

    const options = {
        body: data.body,
        icon: "/static/icon-192.png",
        badge: "/static/icon-192.png",
        tag: "act-doctor-case-" + (data.case_id || "new"),
        renotify: true,
        requireInteraction: true,
        data: {
            url: data.url || "/doctor"
        }
    };

    event.waitUntil(
        self.registration.showNotification(
            data.title || "ACT Doctor Call",
            options
        )
    );
});

self.addEventListener("notificationclick", event => {
    event.notification.close();

    const targetUrl = event.notification.data?.url || "/doctor";

    event.waitUntil(
        clients.matchAll({
            type: "window",
            includeUncontrolled: true
        }).then(windowClients => {
            for (const client of windowClients) {
                if ("focus" in client) {
                    client.navigate(targetUrl);
                    return client.focus();
                }
            }

            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});
