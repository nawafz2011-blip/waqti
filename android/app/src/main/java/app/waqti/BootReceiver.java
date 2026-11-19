package app.waqti;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** بعد ما الجوال يشتغل (أو التطبيق يتحدث): نرجع نراقب الرسائل */
public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        if (!Links.link(c).isEmpty()) WatchService.start(c);
    }
}
