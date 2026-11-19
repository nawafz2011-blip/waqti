package app.waqti;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;

/** شغال دايم بالخلفية: اتصال مفتوح مع قاعدتنا، وكل رسالة جديدة = إشعار بصوت */
public class WatchService extends Service {
    static final String CH_MSG = "msgs", CH_BG = "bg";
    private volatile boolean running;
    private String watching = "";

    static void start(Context c) {
        Intent i = new Intent(c, WatchService.class);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i); else c.startService(i);
    }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        channels();
        Notification n = builder(CH_BG).setContentTitle("وقتي").setContentText("يراقب الرسائل")
                .setContentIntent(open(null, 0)).setOngoing(true).build();
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        else startForeground(1, n);
        String link = Links.link(this);
        if (!link.equals(watching)) {           // رابط جديد: نوقف القديم ونبدأ من جديد
            running = false;
            watching = link;
            if (!link.isEmpty()) {
                running = true;
                for (String t : Links.topics(link)) new Thread(() -> watch(t, link)).start();
            }
        }
        return START_STICKY;
    }

    @Override public void onDestroy() { running = false; watching = ""; super.onDestroy(); }

    private void channels() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager nm = getSystemService(NotificationManager.class);
        NotificationChannel m = new NotificationChannel(CH_MSG, "رسائل وقتي", NotificationManager.IMPORTANCE_HIGH);
        m.enableVibration(true);
        nm.createNotificationChannel(m);
        NotificationChannel b = new NotificationChannel(CH_BG, "تشغيل بالخلفية", NotificationManager.IMPORTANCE_MIN);
        b.setShowBadge(false);
        nm.createNotificationChannel(b);
    }

    private Notification.Builder builder(String ch) {
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, ch) : new Notification.Builder(this);
        return b.setSmallIcon(R.drawable.ic_stat);
    }

    private PendingIntent open(String url, int code) {
        Intent i = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (url != null) i.putExtra("url", url);
        return PendingIntent.getActivity(this, code, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private void watch(String topic, String link) {
        String key = "seen_" + topic;
        long wait = 3000;
        while (running && link.equals(watching)) {
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL(Links.FB + topic + ".json").openConnection();
                c.setRequestProperty("Accept", "text/event-stream");
                c.setConnectTimeout(15000);
                c.setReadTimeout(90000);                 // القاعدة ترسل «أنا موجود» كل 30 ثانية
                BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8));
                String line, event = "";
                while (running && link.equals(watching) && (line = r.readLine()) != null) {
                    if (line.startsWith("event:")) { event = line.substring(6).trim(); continue; }
                    if (!line.startsWith("data:") || !(event.equals("put") || event.equals("patch"))) continue;
                    wait = 3000;
                    JSONObject d = new JSONObject(line.substring(5).trim());
                    String path = d.optString("path", "/");
                    JSONObject items = new JSONObject();
                    Object v = d.opt("data");
                    if (path.equals("/")) { if (v instanceof JSONObject) items = (JSONObject) v; }
                    else if (path.split("/").length == 2 && v instanceof JSONObject) items.put(path.substring(1), v);
                    handle(key, items);
                }
            } catch (Exception ignored) {
            } finally {
                if (c != null) c.disconnect();
            }
            try { Thread.sleep(wait); } catch (InterruptedException e) { return; }
            wait = Math.min(wait * 2, 60000);
        }
    }

    private synchronized void handle(String key, JSONObject items) {
        long seen = Links.prefs(this).getLong(key, -1), newest = seen;
        if (seen < 0) {                          // أول مرة: ما نطلّع الرسائل القديمة كلها
            seen = System.currentTimeMillis() - 10 * 60 * 1000L;
            newest = seen;
        }
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        for (Iterator<String> it = items.keys(); it.hasNext(); ) {
            String id = it.next();
            JSONObject e = items.optJSONObject(id);
            if (e == null || !e.has("m")) continue;
            long t = e.optLong("t", 0);
            if (t <= seen) continue;
            newest = Math.max(newest, t);
            String msg = e.optString("m").replaceFirst("^⏱ وقتي\n", "");
            String click = e.optString("c", "");
            boolean approve = click.contains("github.io/waqti/?") && click.contains("r=");
            Notification.Builder b = builder(CH_MSG).setContentTitle(e.optString("ti", "وقتي"))
                    .setContentText(msg).setStyle(new Notification.BigTextStyle().bigText(msg))
                    .setAutoCancel(true).setWhen(t).setShowWhen(true)
                    .setDefaults(Notification.DEFAULT_ALL)
                    .setContentIntent(open(approve ? click : null, id.hashCode()));
            if (Build.VERSION.SDK_INT < 26) b.setPriority(Notification.PRIORITY_HIGH);
            nm.notify(id.hashCode(), b.build());
        }
        if (newest != Links.prefs(this).getLong(key, -1)) Links.prefs(this).edit().putLong(key, newest).apply();
    }
}
