// "Get the Moo app": where to download the phone app.
//
// The Android app is published as a release of this repository; the link always points at the newest one.
// The iPhone app cannot be handed out yet: that needs Apple's developer program and TestFlight.

const ANDROID_APK = "https://github.com/ajemphilip/moo-moo-dashboard/releases/latest/download/moo-android.apk";
const RELEASES = "https://github.com/ajemphilip/moo-moo-dashboard/releases/latest";

export function GetTheApp() {
  return (
    <div className="moo-card flex flex-col gap-2">
      <h2 className="font-semibold">Get the Moo app</h2>
      <p className="text-sm text-moo-ink/70">
        The app finds the Moo with Bluetooth, sets up its wifi step by step, and lets the phone do Moo&apos;s stories,
        listening and voice with larger models than a browser can hold.
      </p>
      <a
        href={ANDROID_APK}
        className="rounded-moo bg-moo-accent text-white text-center font-semibold px-4 py-3"
      >
        Download for Android
      </a>
      <p className="text-xs text-moo-ink/60">
        Open the downloaded file on the phone. The first time, Android asks you to allow installing apps from your
        browser. <a href={RELEASES} className="underline">What is in this version</a>
      </p>
      <p className="text-xs text-moo-ink/60">iPhone: coming soon. Until then, use this website on the iPhone.</p>
    </div>
  );
}
