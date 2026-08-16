package com.homesh.tv;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Where the server is.
 *
 * <p>Found rather than asked for, wherever possible. The server answers a
 * broadcast on the local network, so this screen usually arrives with the
 * address already filled in and nothing to type. Scanning the subnet would have
 * been the wrong way to do it — slow, and indistinguishable from something
 * unwelcome — but a server that answers when asked is neither.
 *
 * <p>The field stays, because a house can have no server running, two of them,
 * or a network that drops broadcasts.
 *
 * <p>The address is checked before it is saved. A typo caught here is one
 * sentence; a typo saved is a black screen on a television with a remote control
 * as the only way in.
 */
public class SetupActivity extends Activity {

    private EditText field;
    private TextView status;
    private Button connect;
    private final Handler main = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(Color.parseColor("#14100c"));
        int pad = dp(48);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText(R.string.setup_title);
        title.setTextColor(Color.WHITE);
        title.setTextSize(28);
        title.setGravity(Gravity.CENTER);

        TextView hint = new TextView(this);
        hint.setText(R.string.setup_hint);
        hint.setTextColor(Color.parseColor("#a89a86"));
        hint.setTextSize(15);
        hint.setGravity(Gravity.CENTER);
        hint.setPadding(0, dp(8), 0, dp(24));

        field = new EditText(this);
        field.setHint(R.string.setup_placeholder);
        field.setSingleLine(true);
        field.setTextColor(Color.WHITE);
        field.setHintTextColor(Color.parseColor("#6d6152"));
        field.setTextSize(20);
        field.setGravity(Gravity.CENTER);
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        String existing = Prefs.server(this);
        if (existing != null) {
            field.setText(existing);
        }

        connect = new Button(this);
        connect.setText(R.string.setup_connect);
        connect.setOnClickListener(v -> check());

        status = new TextView(this);
        status.setTextColor(Color.parseColor("#d08b26"));
        status.setTextSize(15);
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, dp(16), 0, 0);

        LinearLayout.LayoutParams wide = new LinearLayout.LayoutParams(dp(420),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        root.addView(title);
        root.addView(hint);
        root.addView(field, wide);
        root.addView(connect, new LinearLayout.LayoutParams(dp(220),
                ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(status);

        setContentView(root);
        field.requestFocus();

        // Ask the network before asking the person. Typing a URL on a television
        // is the worst part of setting this up, and the server is on the same
        // wire — so most of the time nobody should have to.
        status.setText(R.string.setup_searching);
        new Thread(() -> {
            String found = Discovery.find();
            main.post(() -> {
                if (found == null) {
                    status.setText(R.string.setup_not_found);
                    return;
                }
                // Filled in rather than accepted silently: the screen should show
                // what it is about to connect to, and a house with two servers
                // should not have one chosen without being seen.
                field.setText(found);
                status.setText(getString(R.string.setup_found, found));
                connect.requestFocus();
            });
        }).start();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void check() {
        String url = Prefs.normalise(field.getText().toString());
        if (url == null) {
            status.setText(R.string.setup_empty);
            return;
        }

        connect.setEnabled(false);
        status.setText(R.string.setup_checking);

        // A plain thread: one request, at most once per press. A pool or a
        // library would be more machinery than this ever needs.
        new Thread(() -> {
            boolean ok = reachable(url);
            main.post(() -> {
                connect.setEnabled(true);
                if (ok) {
                    Prefs.setServer(this, url);
                    startActivity(new Intent(this, MainActivity.class));
                    finish();
                } else {
                    status.setText(getString(R.string.setup_failed, url));
                }
            });
        }).start();
    }

    /** Ask the server to identify itself, so a wrong-but-live address still fails. */
    private boolean reachable(String base) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(base + "/api/health").openConnection();
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(4000);
            conn.setRequestMethod("GET");
            if (conn.getResponseCode() != 200) return false;

            StringBuilder body = new StringBuilder();
            try (InputStream in = conn.getInputStream()) {
                byte[] buf = new byte[512];
                int n;
                while ((n = in.read(buf)) > 0 && body.length() < 4096) {
                    body.append(new String(buf, 0, n, "UTF-8"));
                }
            }
            // Something else answering on that port is a wrong address, not a
            // working one, and saying so here saves a black screen later.
            return body.indexOf("\"status\"") >= 0;
        } catch (IOException | RuntimeException e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    @Override
    public void onBackPressed() {
        // Backing out of setup with nothing configured leaves the app; backing
        // out of a re-run keeps whatever worked before.
        if (Prefs.server(this) == null) {
            finishAffinity();
        } else {
            View focus = getCurrentFocus();
            if (focus != null) focus.clearFocus();
            finish();
        }
    }
}
