// The native main process owns EventKit attribution. Never exec a Python child.
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#include <CommonCrypto/CommonDigest.h>
#include <Python.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <libproc.h>
#include <sys/stat.h>
#include <unistd.h>

#if PY_MAJOR_VERSION != 3 || PY_MINOR_VERSION != 14 || defined(Py_GIL_DISABLED) || defined(Py_DEBUG)
#error The helper requires the reviewed release CPython 3.14 ABI.
#endif

static NSString *const BundleID = @"com.administrateme.cos-reminders-helper";
static const off_t RequestLimit = 1024 * 1024;

static BOOL hexString(id value, NSUInteger length) {
    if (![value isKindOfClass:NSString.class] || [value length] != length) return NO;
    return [value rangeOfCharacterFromSet:
        [[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet]].location == NSNotFound;
}

static BOOL integer(id value) {
    return [value isKindOfClass:NSNumber.class] &&
        CFGetTypeID((__bridge CFTypeRef)value) != CFBooleanGetTypeID() &&
        strcmp([value objCType], @encode(double)) != 0 &&
        strcmp([value objCType], @encode(float)) != 0;
}

static BOOL absolutePath(id value) {
    if (![value isKindOfClass:NSString.class] || ![value isAbsolutePath] ||
        [value length] > PATH_MAX || [value rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location != NSNotFound) return NO;
    return [[value stringByStandardizingPath] isEqualToString:value];
}

static BOOL unchanged(struct stat a, struct stat b) {
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_size == b.st_size &&
        a.st_mtimespec.tv_sec == b.st_mtimespec.tv_sec &&
        a.st_mtimespec.tv_nsec == b.st_mtimespec.tv_nsec &&
        a.st_ctimespec.tv_sec == b.st_ctimespec.tv_sec &&
        a.st_ctimespec.tv_nsec == b.st_ctimespec.tv_nsec;
}

static NSData *readRegular(int fd, off_t limit, BOOL privateFile) {
    struct stat before, after;
    if (fd < 0 || fstat(fd, &before) || !S_ISREG(before.st_mode) || before.st_nlink != 1 ||
        (before.st_uid != getuid() && (privateFile || before.st_uid != 0)) ||
        (before.st_mode & 0022) || (privateFile && (before.st_mode & 0777) != 0600) ||
        before.st_size <= 0 || before.st_size > limit) return nil;
    NSMutableData *data = [NSMutableData dataWithLength:(NSUInteger)before.st_size];
    ssize_t count = pread(fd, data.mutableBytes, data.length, 0);
    if (count != before.st_size || fstat(fd, &after) || !unchanged(before, after)) return nil;
    return data;
}

static NSString *digestFile(int fd) {
    struct stat before, after;
    if (fd < 0 || fstat(fd, &before) || !S_ISREG(before.st_mode) || before.st_nlink != 1 ||
        (before.st_uid != getuid() && before.st_uid != 0) || (before.st_mode & 0022) ||
        before.st_size <= 0 || before.st_size > 256 * 1024 * 1024) return nil;
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    unsigned char buffer[65536], digest[CC_SHA256_DIGEST_LENGTH];
    off_t position = 0;
    while (position < before.st_size) {
        ssize_t count = pread(fd, buffer, sizeof(buffer), position);
        if (count <= 0) return nil;
        CC_SHA256_Update(&context, buffer, (CC_LONG)count);
        position += count;
    }
    if (fstat(fd, &after) || !unchanged(before, after)) return nil;
    CC_SHA256_Final(digest, &context);
    NSMutableString *result = [NSMutableString stringWithCapacity:64];
    for (NSUInteger index = 0; index < sizeof(digest); index++) [result appendFormat:@"%02x", digest[index]];
    return result;
}

static BOOL canonicalDirectory(NSString *path) {
    char resolved[PATH_MAX];
    struct stat metadata;
    return realpath(path.fileSystemRepresentation, resolved) &&
        [path isEqualToString:@(resolved)] && !lstat(resolved, &metadata) &&
        S_ISDIR(metadata.st_mode) && !(metadata.st_mode & 0022) &&
        (metadata.st_uid == getuid() || metadata.st_uid == 0);
}

static NSDictionary *identity(void) {
    NSBundle *bundle = NSBundle.mainBundle;
    char executable[PROC_PIDPATHINFO_MAXSIZE] = {0};
    if (proc_pidpath(getpid(), executable, sizeof(executable)) <= 0) return nil;
    if (![bundle.bundleIdentifier isEqual:BundleID] ||
        ![bundle.infoDictionary[@"NSRemindersFullAccessUsageDescription"] isKindOfClass:NSString.class] ||
        ![bundle.infoDictionary[@"NSRemindersUsageDescription"] isKindOfClass:NSString.class] ||
        ![bundle.executablePath isEqualToString:@(executable)]) return nil;
    SecStaticCodeRef code = NULL;
    OSStatus status = SecStaticCodeCreateWithPath((__bridge CFURLRef)bundle.bundleURL, kSecCSDefaultFlags, &code);
    if (status != errSecSuccess || code == NULL) return nil;
    status = SecStaticCodeCheckValidity(code, kSecCSStrictValidate | kSecCSCheckAllArchitectures, NULL);
    CFRelease(code);
    if (status != errSecSuccess) return nil;
    return @{@"bundle_identifier": BundleID, @"app_path": bundle.bundlePath,
        @"native_executable": @(executable), @"native_pid": @(getpid()),
        @"reminders_full_access_usage": bundle.infoDictionary[@"NSRemindersFullAccessUsageDescription"],
        @"reminders_legacy_usage": bundle.infoDictionary[@"NSRemindersUsageDescription"]};
}

static int failure(int parent, NSString *phase) {
    // Only fixed diagnostics are written, and only after validating the private directory.
    if (parent >= 0) {
        NSData *data = [NSJSONSerialization dataWithJSONObject:
            @{@"contract": @"cos-reminders-helper-error-v1", @"status": @"failed", @"error": phase}
            options:NSJSONWritingSortedKeys error:NULL];
        int output = openat(parent, "result.json", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (output >= 0) { if (write(output, data.bytes, data.length) >= 0) fsync(output); close(output); }
    }
    return 1;
}

static BOOL validRequest(NSDictionary *request) {
    NSSet *keys = [NSSet setWithArray:@[@"contract", @"nonce", @"issued_at_ms", @"expires_at_ms",
        @"operation", @"release_root", @"release_commit", @"python_executable", @"python_library",
        @"python_library_sha256", @"device_identity_sha256", @"os_uid", @"payload"]];
    if (![request isKindOfClass:NSDictionary.class] || ![[NSSet setWithArray:request.allKeys] isEqual:keys] ||
        ![request[@"contract"] isEqual:@"cos-reminders-helper-request-v1"] ||
        !hexString(request[@"nonce"], 32) || !hexString(request[@"release_commit"], 40) ||
        !hexString(request[@"python_library_sha256"], 64) || !hexString(request[@"device_identity_sha256"], 64) ||
        ![@[@"status", @"request", @"inspect", @"read"] containsObject:request[@"operation"]] ||
        ![request[@"payload"] isKindOfClass:NSDictionary.class] ||
        !integer(request[@"issued_at_ms"]) || !integer(request[@"expires_at_ms"]) ||
        !integer(request[@"os_uid"]) || [request[@"os_uid"] longLongValue] != getuid() ||
        !absolutePath(request[@"release_root"]) || !absolutePath(request[@"python_executable"]) ||
        !absolutePath(request[@"python_library"])) return NO;
    long long now = (long long)(NSDate.date.timeIntervalSince1970 * 1000);
    long long issued = [request[@"issued_at_ms"] longLongValue], expires = [request[@"expires_at_ms"] longLongValue];
    return issued > 0 && issued <= now && expires > now && expires > issued && expires - issued <= 120000;
}

static int launch(NSString *path) {
    if (!absolutePath(path) || ![path.lastPathComponent isEqual:@"request.json"]) return 1;
    NSString *directory = path.stringByDeletingLastPathComponent;
    if (!canonicalDirectory(directory)) return 1;
    int parent = open(directory.fileSystemRepresentation, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    struct stat parentStat, requestStat, claimedStat;
    if (parent < 0 || fstat(parent, &parentStat) || parentStat.st_uid != getuid() ||
        (parentStat.st_mode & 0777) != 0700) { if (parent >= 0) close(parent); return 1; }
    int input = openat(parent, "request.json", O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    NSData *data = readRegular(input, RequestLimit, YES);
    if (!data || fstat(input, &requestStat)) { if (input >= 0) close(input); return failure(parent, @"request_file_invalid"); }
    NSDictionary *request = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    if (!validRequest(request)) { close(input); return failure(parent, @"request_contract_invalid"); }
    // RENAME_EXCL makes an existing claim a terminal replay, including after failures.
    if (renameatx_np(parent, "request.json", parent, "claimed.json", RENAME_EXCL) ||
        fstatat(parent, "claimed.json", &claimedStat, AT_SYMLINK_NOFOLLOW) ||
        claimedStat.st_dev != requestStat.st_dev || claimedStat.st_ino != requestStat.st_ino) {
        close(input); return failure(parent, @"request_already_claimed");
    }
    close(input);
    fsync(parent);
    if (!identity()) return failure(parent, @"helper_identity_invalid");
    NSString *root = request[@"release_root"];
    NSString *python = request[@"python_executable"];
    NSString *library = request[@"python_library"];
    if (!canonicalDirectory(root) ||
        !canonicalDirectory([root stringByAppendingPathComponent:@".venv"]) ||
        !canonicalDirectory([root stringByAppendingPathComponent:@".venv/bin"]) ||
        ![python isEqualToString:[root stringByAppendingPathComponent:@".venv/bin/python"]])
        return failure(parent, @"release_coordinate_invalid");
    NSString *venvConfig = [root stringByAppendingPathComponent:@".venv/pyvenv.cfg"];
    int venvFD = open(venvConfig.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    NSData *venvData = readRegular(venvFD, 16384, NO);
    if (venvFD >= 0) close(venvFD);
    if (!venvData) return failure(parent, @"python_coordinate_invalid");
    NSString *marker = [root stringByAppendingPathComponent:@".administrateme-source-commit"];
    int markerFD = open(marker.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    NSData *markerData = readRegular(markerFD, 128, YES);
    if (markerFD >= 0) close(markerFD);
    NSString *markerText = markerData ? [[NSString alloc] initWithData:markerData encoding:NSUTF8StringEncoding] : nil;
    NSString *commit = request[@"release_commit"];
    if (![markerText isEqual:commit] && ![markerText isEqual:[commit stringByAppendingString:@"\n"]])
        return failure(parent, @"release_marker_invalid");
    struct stat pythonStat;
    if (stat(python.fileSystemRepresentation, &pythonStat) || !S_ISREG(pythonStat.st_mode) ||
        !(pythonStat.st_mode & 0111) || (pythonStat.st_mode & 0022) ||
        (pythonStat.st_uid != getuid() && pythonStat.st_uid != 0)) return failure(parent, @"python_coordinate_invalid");
    char realLibrary[PATH_MAX];
    if (!realpath(library.fileSystemRepresentation, realLibrary) || ![library isEqual:@(realLibrary)])
        return failure(parent, @"python_library_invalid");
    int libraryFD = open(realLibrary, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    struct stat libraryBefore, libraryAfter;
    if (libraryFD < 0 || fstat(libraryFD, &libraryBefore) ||
        ![digestFile(libraryFD) isEqual:request[@"python_library_sha256"]]) {
        if (libraryFD >= 0) close(libraryFD); return failure(parent, @"python_library_invalid");
    }
    void *handle = dlopen(realLibrary, RTLD_NOW | RTLD_GLOBAL);
    if (!handle || stat(realLibrary, &libraryAfter) || !unchanged(libraryBefore, libraryAfter)) {
        close(libraryFD); return failure(parent, @"python_library_load_failed");
    }
    close(libraryFD);
    // Resolve the version check first, before using the pinned PyConfig ABI.
    const char *(*getVersion)(void) = dlsym(handle, "Py_GetVersion");
    if (!getVersion || strncmp(getVersion(), "3.14.", 5)) return failure(parent, @"python_version_invalid");
    void (*initConfig)(PyConfig *) = dlsym(handle, "PyConfig_InitIsolatedConfig");
    void (*clearConfig)(PyConfig *) = dlsym(handle, "PyConfig_Clear");
    PyStatus (*setString)(PyConfig *, wchar_t **, const char *) = dlsym(handle, "PyConfig_SetBytesString");
    PyStatus (*setArgv)(PyConfig *, Py_ssize_t, char *const *) = dlsym(handle, "PyConfig_SetBytesArgv");
    PyStatus (*initialize)(const PyConfig *) = dlsym(handle, "Py_InitializeFromConfig");
    int (*runMain)(void) = dlsym(handle, "Py_RunMain");
    PyObject *(*sysGetObject)(const char *) = dlsym(handle, "PySys_GetObject");
    PyObject *(*decodePath)(const char *) = dlsym(handle, "PyUnicode_DecodeFSDefault");
    int (*listAppend)(PyObject *, PyObject *) = dlsym(handle, "PyList_Append");
    void (*decref)(PyObject *) = dlsym(handle, "Py_DecRef");
    if (!initConfig || !clearConfig || !setString || !setArgv || !initialize || !runMain ||
        !sysGetObject || !decodePath || !listAppend || !decref)
        return failure(parent, @"python_api_invalid");
    PyConfig config;
    initConfig(&config);
    config.site_import = 0;
    config.write_bytecode = 0;
    config.parse_argv = 0;
    config.install_signal_handlers = 0;
    NSString *worker = [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"worker.py"];
    NSString *claimed = [directory stringByAppendingPathComponent:@"claimed.json"];
    char *arguments[] = {(char *)worker.fileSystemRepresentation, (char *)claimed.fileSystemRepresentation};
    PyStatus status = setArgv(&config, 2, arguments);
    if (!status._type) status = setString(&config, &config.program_name, python.fileSystemRepresentation);
    if (!status._type) status = setString(&config, &config.executable, python.fileSystemRepresentation);
    if (!status._type) status = setString(&config, &config.run_filename, worker.fileSystemRepresentation);
    if (!status._type) status = initialize(&config);
    clearConfig(&config);
    if (status._type) return failure(parent, @"python_initialization_failed");
    // Isolated 3.14 intentionally ignores pythonpath_env. Append only these
    // reviewed roots after stdlib initialization; never import site/.pth files.
    PyObject *search = sysGetObject("path");
    if (!search) return failure(parent, @"python_path_failed");
    for (NSString *entry in @[root, [root stringByAppendingPathComponent:@".venv/lib/python3.14/site-packages"]]) {
        PyObject *decoded = decodePath(entry.fileSystemRepresentation);
        if (!decoded) return failure(parent, @"python_path_failed");
        int result = listAppend(search, decoded);
        decref(decoded);
        if (result) return failure(parent, @"python_path_failed");
    }
    close(parent);
    return runMain();
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "--identity") == 0) {
            NSDictionary *receipt = identity();
            if (!receipt) return 1;
            NSData *data = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingSortedKeys error:NULL];
            return fwrite(data.bytes, 1, data.length, stdout) == data.length ? 0 : 1;
        }
        if (argc != 2) return 1;
        return launch(@(argv[1]));
    }
}
