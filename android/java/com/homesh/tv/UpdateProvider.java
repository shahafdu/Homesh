package com.homesh.tv;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * Hands the downloaded APK to the system installer.
 *
 * <p>Android has refused {@code file://} URIs for package installs since 7.0, so
 * the file has to arrive as content. AndroidX has a FileProvider that does this;
 * pulling in AndroidX for one class would end this app's freedom from
 * dependencies — and therefore its freedom from Gradle — so it is written out.
 *
 * <p>It serves exactly one file, read-only, from the app's own cache. There is
 * nothing else it can be asked for.
 */
public class UpdateProvider extends ContentProvider {

    static final String FILENAME = "update.apk";

    static Uri uriFor(android.content.Context context) {
        return Uri.parse("content://" + context.getPackageName() + ".updates/" + FILENAME);
    }

    private File file() {
        return new File(getContext().getCacheDir(), FILENAME);
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        // Read-only whatever is asked for: the installer only reads, and a
        // writable descriptor to a package about to be installed would be a
        // gift to anything that could reach it.
        File apk = file();
        if (!apk.isFile()) throw new FileNotFoundException(FILENAME);
        return ParcelFileDescriptor.open(apk, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    /** The installer asks for a name and a size before it opens anything. */
    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] args, String sortOrder) {
        File apk = file();
        MatrixCursor cursor = new MatrixCursor(
                new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
        cursor.addRow(new Object[] {FILENAME, apk.isFile() ? apk.length() : 0});
        return cursor;
    }

    @Override
    public String getType(Uri uri) {
        return "application/vnd.android.package-archive";
    }

    // Nothing else is supported. A provider that exists to pass one file to the
    // installer has no business accepting writes.
    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int delete(Uri uri, String selection, String[] args) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] args) {
        throw new UnsupportedOperationException();
    }
}
