package com.scenecopilot.app;

import android.content.Context;

import androidx.annotation.StringRes;

public enum CaptureProfile {
    ECO(
            "eco",
            R.id.profileEcoButton,
            R.string.capture_profile_eco,
            R.string.capture_profile_eco_hint,
            2800L,
            2200L,
            5600L,
            400L,
            9000L,
            10,
            48 * 1024,
            1100,
            4,
            2
    ),
    BALANCED(
            "balanced",
            R.id.profileBalancedButton,
            R.string.capture_profile_balanced,
            R.string.capture_profile_balanced_hint,
            1800L,
            1200L,
            4200L,
            300L,
            6500L,
            8,
            32 * 1024,
            950,
            6,
            2
    ),
    EXPERT(
            "expert",
            R.id.profileExpertButton,
            R.string.capture_profile_expert,
            R.string.capture_profile_expert_hint,
            1100L,
            800L,
            2600L,
            250L,
            3500L,
            6,
            24 * 1024,
            850,
            8,
            3
    );

    public final String wireId;
    public final int buttonId;
    public final int labelResId;
    public final int hintResId;
    public final long liveBaseMs;
    public final long liveMinMs;
    public final long liveMaxMs;
    public final long liveStepMs;
    public final long liveHeartbeatMs;
    public final int frameHashDiffThreshold;
    public final int audioUploadChunkBytes;
    public final int audioVadRmsThreshold;
    public final int audioVadHangoverFrames;
    public final int audioVadPreRollFrames;

    CaptureProfile(
            String wireId,
            int buttonId,
            @StringRes int labelResId,
            @StringRes int hintResId,
            long liveBaseMs,
            long liveMinMs,
            long liveMaxMs,
            long liveStepMs,
            long liveHeartbeatMs,
            int frameHashDiffThreshold,
            int audioUploadChunkBytes,
            int audioVadRmsThreshold,
            int audioVadHangoverFrames,
            int audioVadPreRollFrames
    ) {
        this.wireId = wireId;
        this.buttonId = buttonId;
        this.labelResId = labelResId;
        this.hintResId = hintResId;
        this.liveBaseMs = liveBaseMs;
        this.liveMinMs = liveMinMs;
        this.liveMaxMs = liveMaxMs;
        this.liveStepMs = liveStepMs;
        this.liveHeartbeatMs = liveHeartbeatMs;
        this.frameHashDiffThreshold = frameHashDiffThreshold;
        this.audioUploadChunkBytes = audioUploadChunkBytes;
        this.audioVadRmsThreshold = audioVadRmsThreshold;
        this.audioVadHangoverFrames = audioVadHangoverFrames;
        this.audioVadPreRollFrames = audioVadPreRollFrames;
    }

    public static CaptureProfile fromButtonId(int buttonId) {
        for (CaptureProfile profile : values()) {
            if (profile.buttonId == buttonId) {
                return profile;
            }
        }
        return null;
    }

    public static CaptureProfile fromWireId(String value) {
        if (value == null) {
            return BALANCED;
        }
        String normalized = value.trim().toLowerCase();
        for (CaptureProfile profile : values()) {
            if (profile.wireId.equals(normalized)) {
                return profile;
            }
        }
        return BALANCED;
    }

    public String displayName(Context context) {
        return context.getString(labelResId);
    }

    public String summary(Context context) {
        return context.getString(
                R.string.capture_profile_summary,
                displayName(context),
                context.getString(hintResId)
        );
    }
}
