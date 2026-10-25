#include "serial.hpp"
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <map>
#include <mutex>
#include <queue>
#include <regex>
#include <stdexcept>
#include <thread>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <termios.h>
#include <unistd.h>
#endif

namespace plotter {
namespace {
class NativeSerial final : public Serial {
#ifdef _WIN32
    HANDLE fd = INVALID_HANDLE_VALUE;
#else
    int fd = -1;
#endif
    std::string buffer;
public:
    explicit NativeSerial(const std::string& port) {
#ifdef _WIN32
        fd = CreateFileA(("\\\\.\\" + port).c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr);
        if (fd == INVALID_HANDLE_VALUE) throw SerialError("Cannot open serial port");
        DCB dcb{}; dcb.DCBlength = sizeof(dcb);
        if (!GetCommState(fd, &dcb)) { CloseHandle(fd); fd = INVALID_HANDLE_VALUE; throw SerialError("GetCommState failed"); }
        dcb.BaudRate = CBR_115200; dcb.ByteSize = 8; dcb.Parity = NOPARITY; dcb.StopBits = ONESTOPBIT;
        dcb.fBinary = TRUE; dcb.fParity = FALSE; dcb.fOutxCtsFlow = FALSE; dcb.fOutxDsrFlow = FALSE;
        dcb.fDtrControl = DTR_CONTROL_DISABLE; dcb.fRtsControl = RTS_CONTROL_DISABLE;
        dcb.fOutX = FALSE; dcb.fInX = FALSE; dcb.fDsrSensitivity = FALSE; dcb.fAbortOnError = FALSE;
        COMMTIMEOUTS t{}; t.ReadIntervalTimeout = MAXDWORD; t.ReadTotalTimeoutConstant = 100; t.WriteTotalTimeoutConstant = 3000;
        if (!SetCommState(fd, &dcb) || !SetCommTimeouts(fd, &t)) {
            CloseHandle(fd); fd = INVALID_HANDLE_VALUE; throw SerialError("Cannot configure serial port");
        }
#else
        fd = ::open(port.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (fd < 0) throw SerialError("Cannot open serial port");
        termios t{};
        if (tcgetattr(fd, &t) != 0) { ::close(fd); fd = -1; throw SerialError("tcgetattr failed"); }
        cfmakeraw(&t); cfsetispeed(&t, B115200); cfsetospeed(&t, B115200);
        t.c_cflag = (t.c_cflag & ~(CSIZE | PARENB | CSTOPB | CRTSCTS)) | CS8 | CLOCAL | CREAD;
        t.c_cflag &= ~HUPCL; t.c_cc[VMIN] = 0; t.c_cc[VTIME] = 1;
        if (tcsetattr(fd, TCSANOW, &t) != 0) { ::close(fd); fd = -1; throw SerialError("Cannot configure serial port"); }
#endif
    }
    ~NativeSerial() override {
#ifdef _WIN32
        if (fd != INVALID_HANDLE_VALUE) CloseHandle(fd);
#else
        if (fd >= 0) ::close(fd);
#endif
    }
    void write(const std::string& bytes) override {
        size_t offset = 0;
        const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (offset < bytes.size()) {
#ifdef _WIN32
            DWORD n = 0;
            if (!WriteFile(fd, bytes.data() + offset, static_cast<DWORD>(bytes.size() - offset), &n, nullptr))
                throw SerialError("Serial write failed");
#else
            auto n = ::write(fd, bytes.data() + offset, bytes.size() - offset);
            if (n < 0 && errno != EAGAIN && errno != EINTR) throw SerialError("Serial write failed");
            if (n < 0) n = 0;
#endif
            offset += static_cast<size_t>(n);
            if (std::chrono::steady_clock::now() > end) throw SerialError("Serial write timeout");
            if (!n) std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
    }
    std::string read_line() override {
        const auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(150);
        while (std::chrono::steady_clock::now() < end) {
            auto newline = buffer.find('\n');
            if (newline != std::string::npos) {
                auto line = buffer.substr(0, newline); buffer.erase(0, newline + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                return line;
            }
            char raw[256];
#ifdef _WIN32
            DWORD n = 0;
            if (!ReadFile(fd, raw, sizeof(raw), &n, nullptr)) throw SerialError("Serial read failed");
#else
            pollfd p{fd, POLLIN, 0};
            auto ready = poll(&p, 1, 100);
            if (ready < 0 && errno != EINTR) throw SerialError("Serial poll failed");
            if (p.revents & (POLLERR | POLLHUP | POLLNVAL)) throw SerialError("Serial device disconnected");
            if (ready <= 0) continue;
            auto n = ::read(fd, raw, sizeof(raw));
            if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            if (n < 0) throw SerialError("Serial read failed");
#endif
            buffer.append(raw, static_cast<size_t>(n));
            if (buffer.size() > 8192) throw SerialError("Oversized serial response");
        }
        return {};
    }
};

class Simulation final : public Serial {
    std::mutex mutex;
    std::queue<std::string> replies;
    double position[3]{}, offset[3]{};
    bool hold = false;
    static std::string xyz(const double* v) {
        char b[128]; std::snprintf(b, sizeof(b), "%.3f,%.3f,%.3f", v[0], v[1], v[2]); return b;
    }
public:
    void write(const std::string& raw) override {
        std::lock_guard<std::mutex> lock(mutex);
        if (raw == "!") { hold = true; return; }
        if (raw == "~") { hold = false; return; }
        if (raw == "\x18") { hold = false; replies={}; offset[0]=offset[1]=offset[2]=0; replies.push("Grbl 1.3a.20221114 ['$' for help]"); return; }
        if (raw == "\x85") { hold = false; return; }
        if (raw == "?") { replies.push("<" + std::string(hold ? "Hold:0" : "Idle") + "|MPos:" + xyz(position) + "|FS:0,0>"); return; }
        if (raw == "$I\n") replies.push("[VER:1.3a.20221114:simulation]");
        else if (raw == "$G\n") replies.push("[GC:G1 G54 G17 G21 G90 G94 M5]");
        else if (raw == "$#\n") {
            replies.push("[G54:0.000,0.000,0.000]"); replies.push("[G92:" + xyz(offset) + "]"); replies.push("[TLO:0.000]");
        } else if (raw == "$N\n") { replies.push("$N0="); replies.push("$N1="); }
        else if (raw == "$$\n") {
            for (auto pair : std::map<int,int>{{3,7},{20,0},{21,0},{22,0},{100,80},{101,80},{102,100},
                                              {110,5000},{111,5000},{112,8000},{130,600},{131,600},{132,300}})
                replies.push("$" + std::to_string(pair.first) + "=" + std::to_string(pair.second));
        } else if (raw == "G92 X0 Y0\n") { offset[0] = position[0]; offset[1] = position[1]; }
        else if (raw.rfind("G1 ", 0) == 0 || raw.rfind("$J=", 0) == 0) {
            std::regex word("([XYZ])(-?[0-9]+(?:\\.[0-9]+)?)");
            for (std::sregex_iterator i(raw.begin(), raw.end(), word), e; i != e; ++i) {
                auto axis = std::string("XYZ").find((*i)[1].str()); double value = std::stod((*i)[2]);
                position[axis] = raw.find("G91") != std::string::npos ? position[axis] + value :
                                 raw.find("G53") != std::string::npos ? value : offset[axis] + value;
            }
        }
        replies.push("ok");
    }
    std::string read_line() override {
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
        std::lock_guard<std::mutex> lock(mutex);
        if (replies.empty()) return {};
        auto line = replies.front(); replies.pop(); return line;
    }
};
}
std::unique_ptr<Serial> open_serial(const std::string& port) { return std::make_unique<NativeSerial>(port); }
std::unique_ptr<Serial> simulated_serial() { return std::make_unique<Simulation>(); }
std::vector<Port> serial_ports() {
    std::vector<Port> ports;
#ifdef _WIN32
    char names[65536];
    DWORD size = QueryDosDeviceA(nullptr, names, sizeof(names));
    if (!size) throw SerialError("Cannot enumerate serial ports");
    const std::regex pattern("COM[0-9]+");
    for (const char* p = names; *p; p += std::char_traits<char>::length(p) + 1)
        if (std::regex_match(p, pattern)) ports.push_back({p, "USB / serial port"});
#else
    std::error_code ec;
    for (const auto& entry : std::filesystem::directory_iterator("/dev", ec)) {
        auto name = entry.path().filename().string();
        if (name.rfind("ttyUSB", 0) == 0 || name.rfind("ttyACM", 0) == 0 || name.rfind("cu.", 0) == 0)
            ports.push_back({entry.path().string(), "USB / serial port"});
    }
#endif
    return ports;
}
}
