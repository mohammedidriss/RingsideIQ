package ai.alion.app;

import android.Manifest;
import android.content.pm.PackageManager;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    private static final int CAMERA_PERMISSION_REQUEST = 1001;
    private PermissionRequest pendingPermissionRequest;

    @Override
    public void onStart() {
        super.onStart();
        // Grant camera access to the WebView so getUserMedia works. Video only: the
        // microphone is never granted (ADR-014 — clips must carry no sound).
        WebView webView = getBridge().getWebView();
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                pendingPermissionRequest = request;
                // Check if the app already has camera permission.
                if (ContextCompat.checkSelfPermission(MainActivity.this, Manifest.permission.CAMERA)
                        == PackageManager.PERMISSION_GRANTED) {
                    grantVideoOnly(request);
                    pendingPermissionRequest = null;
                } else {
                    // Ask the OS for camera permission — triggers the system dialog.
                    ActivityCompat.requestPermissions(
                            MainActivity.this,
                            new String[]{Manifest.permission.CAMERA},
                            CAMERA_PERMISSION_REQUEST
                    );
                }
            }
        });
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == CAMERA_PERMISSION_REQUEST && pendingPermissionRequest != null) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                grantVideoOnly(pendingPermissionRequest);
            } else {
                pendingPermissionRequest.deny();
            }
            pendingPermissionRequest = null;
        }
    }

    /** Grant the page's camera request, never its microphone. */
    private static void grantVideoOnly(PermissionRequest request) {
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)) {
                request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
                return;
            }
        }
        request.deny();
    }
}
