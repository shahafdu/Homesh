package com.homesh.tv;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.util.Log;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * Where the phone keeps what it has learned about the setup, encrypted.
 *
 * <p>The phone has to remember the PC's addresses, the standby's and the other
 * apps': the moment they matter most is when the PC is off and cannot be asked.
 * They are learned from the PC at run time and never shipped in the app. Once
 * on the phone they describe the house, so they are not left lying in a
 * readable file.
 *
 * <p>The key is made in the Android Keystore and never leaves it. It cannot be
 * read out by this app or anybody else, and on most phones it is held in
 * hardware. A copy of the app's files, whether from a backup, a rooted phone
 * or a forensic image, carries only ciphertext. AES-GCM, so a value that has
 * been tampered with fails to decrypt instead of decrypting to something else.
 *
 * <p>Android 6 and later only, which is where the Keystore makes AES keys.
 * Older than that, nothing is stored: the phone asks the PC afresh each time,
 * rather than keeping the setup in the clear.
 */
final class Vault {

    private static final String TAG = "HomeshVault";
    private static final String FILE = "homesh.phone";
    private static final String ALIAS = "homesh.phone.vault";
    private static final String STORE = "AndroidKeyStore";
    /** Marks an encrypted value, and which scheme made it. */
    private static final String PREFIX = "v1:";

    private Vault() {}

    static String get(Context context, String key) {
        String stored = prefs(context).getString(key, null);
        if (stored == null) return null;
        if (!stored.startsWith(PREFIX)) {
            // Written in the clear by a version from before this one: encrypt it
            // where it lies, so upgrading costs nothing that was learned.
            put(context, key, stored);
            return stored;
        }
        try {
            String[] parts = stored.substring(PREFIX.length()).split(":", 2);
            byte[] iv = Base64.decode(parts[0], Base64.NO_WRAP);
            byte[] sealed = Base64.decode(parts[1], Base64.NO_WRAP);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, iv));
            return new String(cipher.doFinal(sealed), StandardCharsets.UTF_8);
        } catch (Exception e) {
            // A key lost with a reset lock screen, or a value that does not
            // verify. Forgotten rather than trusted; the PC can teach it again.
            Log.i(TAG, "could not read " + key + ": " + e.getMessage());
            prefs(context).edit().remove(key).apply();
            return null;
        }
    }

    static void put(Context context, String key, String value) {
        SharedPreferences.Editor edit = prefs(context).edit();
        if (value == null) {
            edit.remove(key).apply();
            return;
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            // The Keystore picks the nonce. It refuses one chosen by the caller,
            // which is what prevents the reuse that would break GCM.
            cipher.init(Cipher.ENCRYPT_MODE, key());
            byte[] sealed = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
            edit.putString(key, PREFIX
                    + Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":"
                    + Base64.encodeToString(sealed, Base64.NO_WRAP)).apply();
        } catch (Exception e) {
            // Better unremembered than remembered in the clear.
            Log.i(TAG, "could not store " + key + ": " + e.getMessage());
            edit.remove(key).apply();
        }
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    private static SecretKey key() throws Exception {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            throw new IllegalStateException("no Keystore AES before Android 6");
        }
        KeyStore store = KeyStore.getInstance(STORE);
        store.load(null);
        if (store.containsAlias(ALIAS)) {
            return ((KeyStore.SecretKeyEntry) store.getEntry(ALIAS, null)).getSecretKey();
        }
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, STORE);
        generator.init(new KeyGenParameterSpec.Builder(ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return generator.generateKey();
    }
}
