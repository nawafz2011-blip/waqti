package app.waqti;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;

/** الرابط اللي حطه المستخدم (رابط المنظم أو رابط الإخوان) وأماكنه في القاعدة */
final class Links {
    static final String FB = "https://waqti-47a62-default-rtdb.europe-west1.firebasedatabase.app/r/";

    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("waqti", Context.MODE_PRIVATE); }

    static String link(Context c) { return prefs(c).getString("link", ""); }

    static String fk(String t) { return t.length() >= 20 ? t : t + "-pub-feed"; }

    static boolean valid(String link) {
        try {
            Uri u = Uri.parse(link.trim());
            return "https".equals(u.getScheme()) && u.getQueryParameter("t") != null;
        } catch (Exception e) { return false; }
    }

    /** الأماكن اللي نراقبها: رسائل البيت، ورسائل المنظم لو الرابط رابطه */
    static String[] topics(String link) {
        Uri u = Uri.parse(link);
        String t = u.getQueryParameter("t"), a = u.getQueryParameter("a");
        if (a != null && !a.isEmpty()) return new String[]{fk(t), fk(a)};
        return new String[]{fk(t)};
    }
}
