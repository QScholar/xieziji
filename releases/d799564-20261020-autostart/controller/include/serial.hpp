#pragma once
#include <memory>
#include <string>
#include <vector>
#include <stdexcept>

namespace plotter {
struct SerialError : std::runtime_error { using std::runtime_error::runtime_error; };
struct Port { std::string device, description; };
class Serial {
public:
    virtual ~Serial() = default;
    virtual void write(const std::string& bytes) = 0;
    // One reader only. Returns an empty string after a bounded timeout.
    virtual std::string read_line() = 0;
};
std::unique_ptr<Serial> open_serial(const std::string& port);
std::unique_ptr<Serial> simulated_serial();
std::vector<Port> serial_ports();
}
