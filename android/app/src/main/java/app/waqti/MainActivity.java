package app.waqti;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/** يفتح صفحة المنظم (أو صفحة الإخوان) داخل التطبيق، ويشغّل المراقبة بالخلفية */
public class MainActivity extends Activity {
    private WebView web;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        if (Links.link(this).isEmpty()) askLink(); else show(getIntent());
    }

    @Override protected void onNewIntent(Intent i) { super.onNewIntent(i); if (!Links.link(this).isEmpty()) show(i); }

    private void show(Intent i) {
        WatchService.start(this);
        askBattery();
        String url = i != null ? i.getStringExtra("url") : null;
        if (web == null) {
            web = new WebView(this);
            WebSettings s = web.getSettings();
            s.setJavaScriptEnabled(true);
            s.setDomStorageEnabled(true);
            web.setBackgroundColor(Color.parseColor("#0f172a"));
            web.setWebViewClient(new WebViewClient() {
                @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                    Uri u = r.getUrl();
                    if ("nawafz2011-blip.github.io".equals(u.getHost())) return false;   // صفحاتنا تفتح هنا
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                    return true;
                }
            });
            web.setOnLongClickListener(v -> { menu(); return true; });
            setContentView(web);
        }
        web.loadUrl(url != null ? url : Links.link(this));
    }

    @Override public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    /** ضغطة طويلة على الصفحة: تغيير الرابط */
    private void menu() {
        new AlertDialog.Builder(this).setTitle("وقتي")
                .setItems(new String[]{"تحديث الصفحة", "تغيير الرابط"}, (d, w) -> {
                    if (w == 0) web.reload(); else askLink();
                }).show();
    }

    private void askLink() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setPadding(48, 96, 48, 48);
        l.setBackgroundColor(Color.parseColor("#0f172a"));
        TextView h = new TextView(this);
        h.setText("⏱ وقتي\n\nالصق الرابط من البرنامج:\n«رابط المنظم» (لك) أو «رابط الإشعارات» (لإخوانك).");
        h.setTextColor(Color.WHITE); h.setTextSize(18); h.setGravity(Gravity.START);
        EditText e = new EditText(this);
        e.setHint("https://nawafz2011-blip.github.io/waqti/msgs.html?...");
        e.setTextColor(Color.WHITE); e.setHintTextColor(Color.GRAY);
        e.setText(Links.link(this));
        ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        try {
            CharSequence clip = cm.getPrimaryClip().getItemAt(0).getText();
            if (Links.link(this).isEmpty() && clip != null && Links.valid(clip.toString())) e.setText(clip);
        } catch (Exception ignored) { }
        TextView err = new TextView(this);
        err.setTextColor(Color.parseColor("#f87171"));
        Button ok = new Button(this);
        ok.setText("حفظ");
        ok.setOnClickListener(v -> {
            String s = e.getText().toString().trim();
            if (!Links.valid(s)) { err.setText("الرابط مو صحيح. انسخه كامل من البرنامج."); return; }
            Links.prefs(this).edit().putString("link", s).apply();
            web = null;
            show(null);
        });
        l.addView(h); l.addView(e); l.addView(ok); l.addView(err);
        setContentView(l);
    }

    /** نطلب ما يوقفنا توفير البطارية، عشان الإشعارات توصل والتطبيق مسكّر */
    private void askBattery() {
        if (Build.VERSION.SDK_INT < 23 || Links.prefs(this).getBoolean("asked_battery", false)) return;
        Links.prefs(this).edit().putBoolean("asked_battery", true).apply();
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        if (pm.isIgnoringBatteryOptimizations(getPackageName())) return;
        try {
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
        } catch (Exception ignored) { }
    }
}
