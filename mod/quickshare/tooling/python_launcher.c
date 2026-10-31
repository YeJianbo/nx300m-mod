/* Avoid cmd.exe's 8191-character command limit for Cargo/rustc arguments. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

int wmain(int argc, wchar_t **argv) {
    const wchar_t *python = _wgetenv(L"NX_QS_PYTHON");
    const wchar_t *name = wcsrchr(argv[0], L'\\');
    const wchar_t *script;
    const wchar_t *tail = GetCommandLineW();
    wchar_t *command;
    size_t length;
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    DWORD result;
    (void)argc;
    name = name ? name + 1 : argv[0];
    script = _wgetenv(wcsstr(name, L"rustc") ? L"NX_QS_RUSTC_SCRIPT" : L"NX_QS_LINK_SCRIPT");
    if (!python || !script) {
        fputws(L"Quick Share build helper environment is missing\n", stderr);
        return 2;
    }
    /* Preserve Cargo's original argument quoting, including --check-cfg lists. */
    while (*tail == L' ' || *tail == L'\t') tail++;
    if (*tail == L'"') {
        tail++;
        while (*tail && *tail != L'"') tail++;
        if (*tail) tail++;
    } else {
        while (*tail && *tail != L' ' && *tail != L'\t') tail++;
    }
    length = wcslen(python) + wcslen(script) + wcslen(tail) + 8;
    command = calloc(length, sizeof(*command));
    if (!command) return 2;
    _snwprintf(command, length, L"\"%ls\" \"%ls\" %ls", python, script, tail);
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    if (!CreateProcessW(python, command, NULL, NULL, TRUE, 0, NULL, NULL, &startup, &process)) {
        fprintf(stderr, "Quick Share build helper CreateProcess failed: %lu\n", GetLastError());
        free(command);
        return 2;
    }
    free(command);
    WaitForSingleObject(process.hProcess, INFINITE);
    GetExitCodeProcess(process.hProcess, &result);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)result;
}
