package com.peva.copybridge;

import android.app.Activity;
import android.content.Intent;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Small local control panel; it never displays or uploads copied chat text. */
public final class MainActivity extends Activity {
    private static final int REQUEST_CAPTURE_ONCE = 701;
    private TextView status;
    private LinearLayout diagnostics;
    private Button diagnosticsButton;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        BridgeAuth.token(this);

        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER_HORIZONTAL);
        int padding = 36;
        layout.setPadding(padding, padding, padding, padding);

        TextView title = new TextView(this);
        title.setText("PEva Helper");
        title.setTextSize(22);
        layout.addView(title);

        TextView details = new TextView(this);
        details.setText("Gotowy do automatycznej pracy z PEvą na komputerze. "
                + "Przy zwykłym użyciu nie wybieraj tutaj żadnej metody: Oczy PEva same "
                + "wykryją widoczny sygnał nowej rozmowy, a potem wykonają jeden lokalny "
                + "OCR tylko wtedy, gdy jest potrzebny. Helper nie wysyła wiadomości ani "
                + "nie przechowuje historii czatu.");
        details.setPadding(0, 24, 0, 24);
        layout.addView(details);

        TextView normalUse = new TextView(this);
        normalUse.setText("Zwykłe użycie: uruchom PEvę na komputerze. "
                + "Ten Helper będzie użyty automatycznie.");
        normalUse.setPadding(0, 0, 0, 18);
        layout.addView(normalUse);

        diagnosticsButton = new Button(this);
        diagnosticsButton.setText("Pokaż diagnostykę");
        diagnosticsButton.setOnClickListener(view -> toggleDiagnostics());
        layout.addView(diagnosticsButton);

        diagnostics = new LinearLayout(this);
        diagnostics.setOrientation(LinearLayout.VERTICAL);
        diagnostics.setVisibility(LinearLayout.GONE);

        TextView extra = new TextView(this);
        extra.setText("Tylko do ręcznych testów lub odzyskania nieczytelnego fragmentu:");
        extra.setPadding(0, 20, 0, 0);
        diagnostics.addView(extra);

        Button readScreenOnce = new Button(this);
        readScreenOnce.setText("Test oczu: odczytaj ekran czatu raz");
        readScreenOnce.setOnClickListener(view -> requestOneScreenRead());
        diagnostics.addView(readScreenOnce);

        Button manualCopy = new Button(this);
        manualCopy.setText("Awaryjnie: odbierz ręczne Copy");
        manualCopy.setOnClickListener(view -> startManualCopyFallback());
        diagnostics.addView(manualCopy);

        Button stop = new Button(this);
        stop.setText("Zatrzymaj odbiór");
        stop.setOnClickListener(view -> {
            stopService(new Intent(this, ClipboardBridgeService.class));
            refreshStatus();
        });
        diagnostics.addView(stop);

        Button clear = new Button(this);
        clear.setText("Usuń tymczasowe dane PEva");
        clear.setOnClickListener(view -> clearTemporaryData());
        diagnostics.addView(clear);
        layout.addView(diagnostics);

        status = new TextView(this);
        status.setPadding(0, 32, 0, 0);
        layout.addView(status);

        setContentView(layout);
        refreshStatus();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshStatus();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST_CAPTURE_ONCE) {
            return;
        }
        if (resultCode != RESULT_OK || data == null) {
            status.setText("Odczyt ekranu anulowany — nic nie zostało przechwycone");
            return;
        }
        Intent service = new Intent(this, ScreenOcrService.class)
                .setAction(ScreenOcrService.ACTION_CAPTURE_ONCE)
                .putExtra(ScreenOcrService.EXTRA_RESULT_CODE, resultCode)
                .putExtra(ScreenOcrService.EXTRA_RESULT_DATA, data);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(service);
        } else {
            startService(service);
        }
        status.setText("Jednorazowy odczyt uruchomiony — wracam do Last Z");
        moveTaskToBack(true);
    }

    private void requestOneScreenRead() {
        MediaProjectionManager projection = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        if (projection == null) {
            status.setText("Ten Android nie udostępnia przechwycenia ekranu");
            return;
        }
        startActivityForResult(projection.createScreenCaptureIntent(), REQUEST_CAPTURE_ONCE);
    }

    /** Manual recovery only; normal PEva input uses the automatic OCR path. */
    private void startManualCopyFallback() {
        startBridge(20000, 12);
        status.setText("Awaryjny odbiór Copy jest włączony przez 20 sekund. "
                + "To nie jest zwykły tryb Oczu.");
        moveTaskToBack(true);
    }

    private void toggleDiagnostics() {
        boolean isVisible = diagnostics.getVisibility() == LinearLayout.VISIBLE;
        diagnostics.setVisibility(isVisible ? LinearLayout.GONE : LinearLayout.VISIBLE);
        diagnosticsButton.setText(isVisible ? "Pokaż diagnostykę" : "Ukryj diagnostykę");
    }

    private void startBridge(int windowMs, int maxCopies) {
        Intent service = new Intent(this, ClipboardBridgeService.class)
                .setAction(ClipboardBridgeService.ACTION_ARM)
                .putExtra(BridgeCommandReceiver.EXTRA_WINDOW_MS, windowMs)
                .putExtra(BridgeCommandReceiver.EXTRA_MAX_COPIES, maxCopies);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(service);
        } else {
            startService(service);
        }
        refreshStatus();
    }

    private void refreshStatus() {
        String state = getSharedPreferences(ClipboardBridgeService.PREFS, MODE_PRIVATE)
                .getString("status", "Gotowy — schowek nie jest odczytywany");
        int length = getSharedPreferences(ClipboardBridgeService.PREFS, MODE_PRIVATE)
                .getInt("last_length", 0);
        int pending = ClipboardInbox.pendingCount(this);
        OcrResultStore.Result screenRead = OcrResultStore.read(this);
        String ocrState = screenRead.capturedAt > 0
                ? screenRead.status + " — czeka na lokalny odbiór PEva"
                : "Brak oczekującego odczytu";
        status.setText("Stan techniczny: " + state
                + "\nOstatnia długość: " + length + " znaków"
                + "\nPrywatna kolejka dla PEva: " + pending
                + "\nOCR ekranu: " + ocrState
                + "\n\nNormalne Oczy uruchamia PEva na komputerze. Treść nie jest "
                + "wyświetlana ani wysyłana przez ten Helper.");
    }

    private void clearTemporaryData() {
        stopService(new Intent(this, ClipboardBridgeService.class));
        stopService(new Intent(this, ScreenOcrService.class));
        ClipboardInbox.clear(this);
        OcrResultStore.clear(this);
        getSharedPreferences(ClipboardBridgeService.PREFS, MODE_PRIVATE).edit()
                .putString("status", "Tymczasowe dane PEva usunięte — schowek nie został zmieniony")
                .putInt("last_length", 0)
                .putLong("captured_at", 0L)
                .putInt("captured_in_window", 0)
                .putLong("armed_until", 0L)
                .apply();
        refreshStatus();
    }
}
