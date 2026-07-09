import AppKit
import Combine
import Foundation

final class AppUpdateStore: ObservableObject {
    @Published private(set) var result: AppUpdateResult = .idle()
    @Published private(set) var isChecking = false

    private let settings: AppSettings
    private let checker: GitHubReleaseUpdateChecker

    init(
        settings: AppSettings,
        checker: GitHubReleaseUpdateChecker = GitHubReleaseUpdateChecker()
    ) {
        self.settings = settings
        self.checker = checker
    }

    func startAutomaticCheck() {
        check(force: false)
    }

    func checkNow() {
        check(force: true)
    }

    func openPreferredUpdateURL() {
        guard let url = result.preferredOpenURL else { return }
        NSWorkspace.shared.open(url)
    }

    func skipCurrentAvailableVersion() {
        guard let version = result.latestVersionLabel else { return }
        settings.skipUpdateVersion(version)
        if result.status == .updateAvailable {
            result = AppUpdateResult(
                status: .upToDate,
                checkedAt: result.checkedAt,
                currentVersion: result.currentVersion,
                latestRelease: result.latestRelease,
                preferredAsset: result.preferredAsset,
                errorMessage: nil
            )
        }
    }

    private func check(force: Bool) {
        guard !isChecking else { return }
        isChecking = true
        result = AppUpdateResult(
            status: .checking,
            checkedAt: Date(),
            currentVersion: AppVersion.current(),
            latestRelease: result.latestRelease,
            preferredAsset: result.preferredAsset,
            errorMessage: nil
        )

        checker.check(
            currentVersion: AppVersion.current(),
            includePrereleases: true,
            force: force
        ) { [weak self] result in
            DispatchQueue.main.async {
                self?.apply(result, force: force)
            }
        }
    }

    private func apply(_ nextResult: AppUpdateResult, force: Bool) {
        isChecking = false
        if !force,
           nextResult.status == .updateAvailable,
           nextResult.latestVersionLabel == settings.skippedUpdateVersion {
            result = AppUpdateResult(
                status: .upToDate,
                checkedAt: nextResult.checkedAt,
                currentVersion: nextResult.currentVersion,
                latestRelease: nextResult.latestRelease,
                preferredAsset: nextResult.preferredAsset,
                errorMessage: nil
            )
            return
        }
        result = nextResult
    }
}
