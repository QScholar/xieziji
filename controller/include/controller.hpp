#pragma once
#include "serial.hpp"
#include "json.hpp"
#include <atomic>
#include <filesystem>
#include <fstream>
#include <functional>
#include <mutex>
#include <map>
#include <vector>
#include <thread>

namespace plotter {
using Json = nlohmann::json;
namespace fs = std::filesystem;
struct Cancelled {};
class Controller {
public:
    using Factory = std::function<std::unique_ptr<Serial>(const std::string&)>;
    Controller(fs::path jobs, bool simulate = false, Factory factory = {}, std::string extra_port = "", bool contact_startup = false);
    ~Controller();
    Json state(bool refresh = false);
    Json ports();
    Json call(const std::string& action, const Json& data);
    Json upload(const std::string& id, const std::string& action, const Json& data);
private:
    fs::path root;
    bool simulate;
    bool contact_startup;
    std::atomic<bool> calibrated{false};
    double top_z = 0;
    bool expected_reset = false;
    Factory factory;
    std::string extra_port;
    std::unique_ptr<Serial> serial;
    std::mutex api_mutex, state_mutex, tx_mutex, log_mutex, jobs_mutex;
    std::thread worker;
    std::atomic<bool> busy{false}, stopped{false}, stop_requested{false}, paused{false}, manual_cancel{false};
    Json current;
    Json device_control = {{"controller_type","kenjoy-grbl"},{"pen_up",0},{"pen_down",10},{"pen_feed",600}};
    int override_pending = -1; // Protected by tx_mutex; await feedback before another increment.
    std::ofstream log;
    std::chrono::steady_clock::time_point last_poll{};
    void update(const Json& fields);
    void cancelled();
    void record(const char* direction, const std::string& bytes);
    void write(const std::string& bytes, bool realtime = false);
    std::string read();
    std::string status();
    std::vector<std::string> command(const std::string& line, bool running = false, bool manual = false);
    Json check(const Json& metadata, bool pen_up = true);
    void wait_idle(bool writing);
    void run_job(fs::path folder, Json origin);
    void return_after_stop(const fs::path& folder, const Json& origin);
    void run_manual(std::vector<std::string> steps);
    void prepare_contact();
    double machine_z(double logical) const;
    void verify_offsets(const std::map<std::string,std::vector<double>>& offsets);
    void failure(const std::string& message, bool manual);
    void save(const fs::path& folder);
    fs::path job_path(const std::string& id);
    Json manifest(const fs::path& folder);
    double validate_job(const fs::path& folder, const Json& meta);
    void join_finished();
};
}
