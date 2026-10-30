#include "controller.hpp"
#include "picosha2.h"
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <locale>
#include <regex>
#include <sstream>
#include <stdexcept>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <fcntl.h>
#include <unistd.h>
#endif

namespace plotter {
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;
namespace {
constexpr double MaxTaskFeed = 12000;
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
double numeric(const Json& data, const char* key, double low, double high);
std::string decimal(double value) {
    std::ostringstream out; out.imbue(std::locale::classic()); out << std::setprecision(9) << value; return out.str();
}
Json control_config(const Json& value) {
    require(value.is_object(),"Invalid device control settings");
    auto type=value.value("controller_type",std::string("kenjoy-grbl"));
    require(type=="kenjoy-grbl" || type=="grbl","Unsupported controller type");
    auto full=Json{{"pen_up",0},{"pen_down",10},{"pen_feed",600}}; full.update(value);
    double up=numeric(full,"pen_up",-300,300), down=numeric(full,"pen_down",-300,300), feed=numeric(full,"pen_feed",100,3000);
    require(std::abs(up-down)>=.01,"Pen positions must differ");
    return {{"controller_type",type},{"pen_up",up},{"pen_down",down},{"pen_feed",feed}};
}
bool starts(const std::string& value, const std::string& prefix) { return value.rfind(prefix, 0) == 0; }
double numeric(const Json& data, const char* key, double low, double high) {
    require(data.contains(key) && data[key].is_number(), "Missing numeric parameter");
    double value = data[key].get<double>();
    require(std::isfinite(value) && value >= low && value <= high, "Numeric parameter out of range"); return value;
}
void atomic_json(const fs::path& path, const Json& value) {
    auto temp = path; temp += ".tmp";
    { std::ofstream f(temp, std::ios::binary | std::ios::trunc); f << value.dump(2); f.flush(); require(bool(f), "Cannot save controller state"); }
#ifdef _WIN32
    // Windows rename does not replace an existing file. A missing state file
    // never means a task can replay: execution is claimed by a separate marker.
    std::error_code ec; fs::remove(path, ec);
#endif
    fs::rename(temp, path);
}
void claim_job(const fs::path& folder) {
    auto path = folder / "consumed";
    const char* message = "claimed; never automatically replay\n";
#ifdef _WIN32
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH, nullptr);
    require(file != INVALID_HANDLE_VALUE,"Task already claimed or claim file cannot be created");
    DWORD written = 0;
    bool ok = WriteFile(file,message,static_cast<DWORD>(std::char_traits<char>::length(message)),&written,nullptr)
              && written == std::char_traits<char>::length(message) && FlushFileBuffers(file);
    CloseHandle(file); require(ok,"Cannot persist task claim");
#else
    int fd = ::open(path.c_str(),O_WRONLY | O_CREAT | O_EXCL,0600);
    require(fd>=0,"Task already claimed or claim file cannot be created");
    auto size = std::char_traits<char>::length(message);
    bool ok = ::write(fd,message,size)==static_cast<ssize_t>(size) && ::fsync(fd)==0;
    ::close(fd); require(ok,"Cannot persist task claim");
    int directory=::open(folder.c_str(),O_RDONLY | O_DIRECTORY);
    require(directory>=0,"Cannot open task directory"); ok=::fsync(directory)==0; ::close(directory);
    require(ok,"Cannot persist task directory");
#endif
}
std::vector<double> values(const std::string& text) {
    std::vector<double> result; std::istringstream stream(text); std::string part;
    while (std::getline(stream, part, ',')) {
        size_t used = 0; double value = std::stod(part, &used);
        require(used == part.size() && std::isfinite(value), "Invalid controller coordinates"); result.push_back(value);
    }
    return result;
}
}

Controller::Controller(fs::path jobs, bool simulation, Factory serial_factory, std::string additional_port, bool contact)
    : root(fs::absolute(jobs)), simulate(simulation), contact_startup(contact), factory(std::move(serial_factory)), extra_port(std::move(additional_port)) {
    fs::create_directories(root);
    current = {{"connected",false},{"mode",simulate ? "simulation" : "serial"},{"phase","disconnected"},
               {"status",""},{"port",""},{"job_id",nullptr},{"acknowledged",0},{"total",0},{"error",nullptr},
               {"operation_type",nullptr},{"position",nullptr},{"pen","unknown"},{"pen_top",nullptr},
               {"motion_revision",0},{"last_action",""},{"controller","cpp"},{"protocol_version",1},
               {"feed_override",nullptr},{"feed_override_requested",100},{"speed_limit",MaxTaskFeed},
               {"startup_mode",contact_startup?"paper-contact":"legacy"},{"calibrated",false},{"logical_z",nullptr}};
}
double Controller::machine_z(double logical) const { return logical + (contact_startup ? top_z : 0); }
void Controller::verify_offsets(const std::map<std::string,std::vector<double>>& offsets) {
    auto g54=offsets.at("G54"), g92=offsets.at("G92"), tlo=offsets.at("TLO");
    auto axes=g54.size();
    require((axes==3 || (device_control["controller_type"]=="grbl" && axes==4)) && g92.size()==axes && tlo.size()==1,"Missing coordinate offsets");
    require(std::abs(tlo[0])<=.01,"Tool length offset must be zero");
    if (contact_startup) {
        require(calibrated,"Confirm paper contact and prepare the machine first");
        require(std::abs(g54[2]+g92[2]-top_z)<=.01,"Z reference changed; prepare the machine again");
    } else {
        require(std::abs(g54[2])<=.01,"G54 Z offset must be zero");
        require(std::abs(g92[2])<=.01,"Nonzero Z offset");
    }
}
Controller::~Controller() {
    try { std::lock_guard<std::mutex> lock(tx_mutex); stopped = true; paused = false; if (serial && busy) serial->write("!"); } catch (...) {}
    if (worker.joinable()) worker.join();
}
void Controller::update(const Json& fields) { std::lock_guard<std::mutex> lock(state_mutex); current.update(fields); }
Json Controller::state(bool refresh) {
    if (refresh && api_mutex.try_lock()) {
        std::unique_lock<std::mutex> guard(api_mutex, std::adopt_lock);
        auto snapshot = state();
        if (!busy && snapshot["connected"] == true && snapshot["phase"] != "failed" && Clock::now() - last_poll > 500ms) {
            try { status(); last_poll = Clock::now(); } catch (const std::exception& e) { failure(e.what(), false); }
        }
    }
    std::lock_guard<std::mutex> lock(state_mutex); return current;
}
Json Controller::ports() {
    Json result = Json::array();
    if (simulate) result.push_back({{"device","SIMULATOR"},{"description","C++ simulated GRBL"}});
    else {
        for (auto& p : serial_ports()) result.push_back({{"device",p.device},{"description",p.description}});
        if (!extra_port.empty()) result.push_back({{"device",extra_port},{"description","Explicit serial device"}});
    }
    return {{"ports",result}};
}
void Controller::cancelled() { if (stopped || stop_requested) throw Cancelled{}; }
void Controller::record(const char* direction, const std::string& raw) {
    std::lock_guard<std::mutex> lock(log_mutex);
    if (log.is_open()) {
        static const char* digits = "0123456789abcdef";
        std::string hex; for (unsigned char c : raw) { hex += digits[c >> 4]; hex += digits[c & 15]; }
        log << Json({{"time",std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count()},
                     {"direction",direction},{"hex",hex}}).dump() << '\n'; log.flush();
    }
}
void Controller::write(const std::string& raw, bool realtime) {
    std::lock_guard<std::mutex> lock(tx_mutex);
    if (!realtime) cancelled();
    require(bool(serial), "Serial device is not connected"); record("tx", raw); serial->write(raw);
}
std::string Controller::read() {
    cancelled(); auto line = serial->read_line(); record("rx", line); cancelled();
    if (contact_startup && calibrated && !expected_reset && starts(line,"Grbl ")) {
        calibrated=false;update({{"calibrated",false},{"logical_z",nullptr}});
        throw SerialError("Controller restarted; repeat paper-contact startup");
    }
    if (starts(line, "<")) {
        Json fields = {{"status",line},{"position",nullptr},{"pen","unknown"},{"pen_top",nullptr}};
        std::smatch match; const std::regex position("MPos:([-0-9.]+),([-0-9.]+),([-0-9.]+)");
        if (std::regex_search(line, match, position)) {
            auto p = values(match[1].str() + "," + match[2].str() + "," + match[3].str());
            fields["position"] = p;
            bool known=!contact_startup || calibrated;
            fields["logical_z"]=known?Json(p[2]-(contact_startup?top_z:0)):Json(nullptr);
            fields["pen_top"]=known?Json(std::abs(p[2]-machine_z(device_control["pen_up"]))<=.05):Json(nullptr);
            fields["pen"] = !known?"unknown":std::abs(p[2]-machine_z(device_control["pen_up"]))<=.05?"up":std::abs(p[2]-machine_z(device_control["pen_down"]))<=.05?"down":"unknown";
        }
        std::smatch overrides;
        if (std::regex_search(line, overrides, std::regex("\\|Ov:([0-9]+),[0-9]+,[0-9]+"))) {
            fields["feed_override"] = std::stoi(overrides[1]);
        }
        update(fields);
        // Use controller feedback between increments: GRBL coalesces repeated
        // realtime bytes received in one cycle. Never queue blind increments.
        if (fields.contains("feed_override")) {
            std::lock_guard<std::mutex> tx(tx_mutex);
            auto s = state();
            if (override_pending == s["feed_override"].get<int>()) override_pending = -1;
            if (!stopped && !stop_requested && s["operation_type"] == "writing" &&
                override_pending == -1 && (s["phase"] == "running" || s["phase"] == "paused" || s["phase"] == "draining")) {
                int delta = s["feed_override_requested"].get<int>() - s["feed_override"].get<int>();
                if (delta) {
                    char code = delta >= 10 ? '\x91' : delta <= -10 ? '\x92' : delta > 0 ? '\x93' : '\x94';
                    override_pending = s["feed_override"].get<int>() + (delta >= 10 ? 10 : delta <= -10 ? -10 : delta > 0 ? 1 : -1);
                    std::string raw(1, code); record("tx",raw); serial->write(raw);
                }
            }
        }
    }
    if (starts(line, "error:") || starts(line, "ALARM:")) throw SerialError(line);
    return line;
}
std::string Controller::status() {
    write("?"); auto deadline = Clock::now() + 4s;
    while (Clock::now() < deadline) { auto line = read(); if (starts(line, "<")) return line; }
    throw SerialError("Controller status timeout");
}
std::vector<std::string> Controller::command(const std::string& line, bool running, bool manual) {
    require(line.size() + 1 <= 78, "G-code line too long");
    // Pause and cancellation share the write lock: no new motion can slip past
    // an accepted hold/cancel request, even between the caller's checks.
    for (;;) {
        cancelled();
        {
            std::lock_guard<std::mutex> lock(tx_mutex); cancelled();
            if (manual && manual_cancel) return {};
            if (!running || !paused) {
                std::string sent=line;
                // Jobs retain logical Z0/Z13.5; translate only Z moves to the
                // session's physical machine frame. XY remains in G54/G92.
                if (contact_startup && calibrated && starts(line,"G1 Z")) {
                    std::smatch m;
                    require(std::regex_match(line,m,std::regex("G1 Z(-?[0-9]+(?:\\.[0-9]+)?) F([0-9]+(?:\\.[0-9]+)?)")),"Invalid logical Z command");
                    sent="G53 G1 Z"+decimal(machine_z(std::stod(m[1])))+" F"+m[2].str();
                }
                record("tx",sent+"\n"); serial->write(sent+"\n"); break;
            }
        }
        status(); std::this_thread::sleep_for(20ms);
    }
    auto deadline = Clock::now() + 30s, poll = Clock::now(); std::vector<std::string> replies;
    for (;;) {
        auto reply = read();
        if (!reply.empty()) { replies.push_back(reply); require(replies.size() <= 4096, "Too many controller responses"); if (reply == "ok") return replies; }
        auto now = Clock::now();
        if (running && now-poll > 500ms) { write("?"); poll = now; }
        if (running && paused) deadline = now + 30s;
        if (now > deadline) throw SerialError("Command acknowledgement timeout");
    }
}
Json Controller::check(const Json& metadata, bool pen_up) {
    auto report = status(); require(starts(report, "<Idle"), "Machine must be Idle");
    auto p = state()["position"]; require(p.is_array() && p.size() == 3, "Missing machine position");
    double up=machine_z(device_control["pen_up"]), down=machine_z(device_control["pen_down"]);
    require(pen_up ? std::abs(p[2].get<double>()-up)<=.01 : p[2].get<double>()>=std::min(up,down)-.01 && p[2].get<double>()<=std::max(up,down)+.01, "Raise pen to configured up position first");
    auto modal = command("$G");
    require(std::any_of(modal.begin(), modal.end(), [](const auto& s){return std::regex_search(s, std::regex("\\bG54\\b"));}), "Expected G54 work coordinates");
    auto parameters = command("$#"); std::map<std::string,std::vector<double>> offsets;
    for (auto& line : parameters) { std::smatch m; if (std::regex_match(line,m,std::regex("\\[(G54|G92|TLO):([0-9.,-]+)\\]"))) offsets[m[1]] = values(m[2]); }
    // G92 X0 Y0 establishes the task origin relative to the existing G54 XY
    // offset. Keep that saved work origin; only Z/tool offsets affect pen height.
    verify_offsets(offsets);
    auto replies = command("$$"); std::map<int,double> settings;
    for (auto& line : replies) { std::smatch m; if (std::regex_match(line,m,std::regex("\\$([0-9]+)=([0-9.]+)"))) settings[std::stoi(m[1])] = std::stod(m[2]); }
    auto paper = metadata.value("machine_paper",metadata.value("paper",Json::array({210,297})));
    require(paper.is_array() && paper.size() == 2, "Invalid machine dimensions");
    for (int i=0;i<2;++i) require(paper[i].is_number() && std::isfinite(paper[i].get<double>()) && paper[i].get<double>() > 0 && paper[i].get<double>() <= 600, "Invalid machine dimensions");
    for (auto pair : std::map<int,double>{{130,paper[0].get<double>()},{131,paper[1].get<double>()},{132,std::max(std::abs(up),std::abs(down))},{110,3000},{111,3000},{112,3000}})
        require(settings.count(pair.first) && settings[pair.first] >= pair.second, "Insufficient travel or speed");
    if (device_control["controller_type"]=="kenjoy-grbl") for (auto pair : std::map<int,double>{{3,7},{20,0},{21,0},{22,0},{100,80},{101,80},{102,100}})
        require(settings.count(pair.first) && settings[pair.first] == pair.second, "Unverified machine configuration");
    auto startup = command("$N");
    require(std::find(startup.begin(),startup.end(),"$N0=") != startup.end() && std::find(startup.begin(),startup.end(),"$N1=") != startup.end(), "Missing startup configuration");
    for (auto& line : startup) require(line == "$N0=" || line == "$N1=" || line == "ok", "Unverified startup command");
    return {{"status",report},{"position",p},{"settings",replies},{"modal",modal},{"parameters",parameters},{"startup",startup}};
}
void Controller::wait_idle(bool writing) {
    auto deadline = Clock::now() + (writing ? 90s : 35s);
    for (;;) {
        auto report = status();
        if (starts(report,"<Idle") && (!writing || !paused)) return;
        require(!starts(report,"<Alarm") && !starts(report,"<Door") && (writing || !starts(report,"<Hold")), "Motion did not finish normally");
        if (writing && paused) deadline = Clock::now() + 90s;
        require(Clock::now() < deadline, "Physical motion completion timeout"); std::this_thread::sleep_for(50ms);
    }
}
void Controller::join_finished() {
    if (busy) {
        auto snapshot=state();
        // Terminal motion state can be visible while the worker still saves
        // its final file and closes the log. Join that cleanup before allowing
        // the next operation; running/paused/draining work still rejects it.
        require(snapshot["phase"]=="complete" || snapshot["phase"]=="idle" || snapshot["phase"]=="stopped", "Another operation is active");
    }
    if (worker.joinable()) worker.join();
}
Json Controller::call(const std::string& action, const Json& data) {
    require(data.is_object(), "Expected JSON object");
    if (action == "speed") {
        double percent = numeric(data,"percent",10,200);
        require(percent == std::floor(percent), "Speed percentage must be an integer");
        std::lock_guard<std::mutex> tx(tx_mutex); auto s = state();
        require(busy && s["connected"] == true && s["operation_type"] == "writing" &&
                (s["phase"] == "running" || s["phase"] == "paused" || s["phase"] == "draining"), "No adjustable writing task is active");
        require(data.value("job_id",std::string()) == s["job_id"], "Writing task has changed");
        require(s["feed_override"].is_number_integer(), "Firmware does not report realtime speed overrides");
        require(percent <= s.value("speed_max_percent",200), "Speed would exceed 12000 mm/min");
        update({{"feed_override_requested",static_cast<int>(percent)}});
        return state();
    }
    // These commands never wait for serial queries or uploads.
    if (action == "stop" || action == "pause" || action == "resume" || action == "manual-pause" || action == "manual-stop") {
        std::lock_guard<std::mutex> lock(tx_mutex); auto s = state();
        if (action == "stop" && (s["phase"] == "stopping" || s["phase"] == "stopped")) return s;
        require(busy, "No operation is active");
        if (starts(action,"manual-")) {
            require(s["operation_type"] == "manual", "No manual operation is active"); manual_cancel = true;
            if (serial) { record("tx","\x85"); serial->write("\x85"); }
            update({{"phase","manual_stopping"},{"last_action","Manual movement cancelled"}});
        } else {
            require(s["operation_type"] == "writing", "No writing task is active");
            require(s["phase"] != "stopping" && s["phase"] != "checking", "Task cannot be controlled in this phase");
            if (action == "stop") {
                // A second stop during the return cancels that jog instead of
                // restarting recovery. Repeated requests while stopping are inert.
                if (s["phase"] == "returning") {
                    stopped=true; paused=false;
                    update({{"phase","stopping"},{"last_action","Return to origin cancelled"}});
                    try { record("tx","\x85"); serial->write("\x85"); } catch (const std::exception& e) { update({{"error",e.what()}}); }
                    return state();
                }
                stop_requested = true; paused = false;
                update({{"phase","stopping"},{"motion_revision",s["motion_revision"].get<int>()+1}});
                try { record("tx","!"); serial->write("!"); }
                catch (const std::exception& e) { stopped=true; stop_requested=false; update({{"error",e.what()}}); }
            } else {
                require(action != "resume" || s["phase"] == "paused", "Task is not paused");
                paused = action == "pause";
                try { record("tx",paused ? "!" : "~"); serial->write(paused ? "!" : "~"); }
                catch (const std::exception& e) { stopped = true; paused = false; update({{"phase","stopping"},{"error",e.what()}}); throw; }
                update({{"phase",paused ? "paused" : "running"}});
            }
        }
        return state();
    }
    std::lock_guard<std::mutex> operation(api_mutex);
    try {
    if (action == "connect") {
        join_finished(); require(!serial && state()["connected"] == false, "Disconnect the current device first");
        auto port = data.at("port").get<std::string>(); bool allowed = false;
        auto available = ports();
        for (auto& entry : available["ports"]) if (entry["device"] == port) allowed = true;
        require(allowed, "Serial port is not in the allowed device list"); stopped = false; stop_requested = false; manual_cancel = false;
        auto requested=data.value("device_control",Json::object());
        if (contact_startup && requested.empty()) requested={{"controller_type","grbl"},{"pen_up",0},{"pen_down",13.5},{"pen_feed",100}};
        device_control=control_config(requested);
        if (contact_startup) require(device_control["controller_type"]=="grbl" && device_control["pen_up"]==0 && device_control["pen_down"]==13.5,"Paper-contact startup requires GRBL, pen up Z0 and pen down Z13.5");
        calibrated=false;expected_reset=false;
        update({{"calibrated",false},{"logical_z",nullptr}});
        update({{"device_control",device_control}});
        serial = factory ? factory(port) : simulate ? simulated_serial() : open_serial(port);
        try {
            if (!simulate && !factory) { auto end = Clock::now()+2s; while (Clock::now()<end) read(); }
            auto initial=status(); bool startup_reset=false;
            if (contact_startup && (starts(initial,"<Door:0|") || starts(initial,"<Hold:0|"))) {
                // The paper-contact workflow explicitly abandons the old task.
                // Only a confirmed stationary suspension can be reset here;
                // never resume (~), bypass an open door, or reset moving axes.
                auto before=state()["position"];
                write("\x18",true);auto deadline=Clock::now()+8s;bool banner=false;
                while(Clock::now()<deadline){if(starts(read(),"Grbl ")){banner=true;break;}}
                require(banner,"Startup reset confirmation missing");
                initial=status();auto after=state()["position"];
                require(before.is_array() && after.is_array(),"Missing startup position");
                for(size_t i=0;i<3;++i)require(std::abs(before[i].get<double>()-after[i].get<double>())<=.01,"Position changed after startup reset; confirm paper contact again");
                startup_reset=true;
            }
            require(starts(initial,"<Idle"), "Machine must be Idle");
            auto info = command("$I"); if (device_control["controller_type"]=="kenjoy-grbl") require(std::any_of(info.begin(),info.end(),[](const auto& s){return s.find("[VER:1.3a.20221114:") != std::string::npos;}), "Unverified firmware version");
            update({{"connected",true},{"phase","idle"},{"port",port},{"error",nullptr},
                    {"startup_reset",startup_reset},{"last_action",startup_reset?"Previous suspended task discarded; confirm paper contact before preparation":""}});
        } catch (...) { serial.reset(); update({{"connected",false},{"position",nullptr},{"pen_top",nullptr},{"pen","unknown"}}); throw; }
        return state();
    }
    if (action == "disconnect") {
        join_finished(); require(contact_startup || state()["phase"] != "failed", "Handle the machine fault and restart the controller service"); serial.reset();
        calibrated=false; update({{"calibrated",false},{"logical_z",nullptr}});
        update({{"connected",false},{"phase","disconnected"},{"port",""},{"status",""},{"position",nullptr},{"pen","unknown"},{"pen_top",nullptr}}); return state();
    }
    if (action == "prepare") {
        join_finished(); require(contact_startup,"This device does not use paper-contact startup");
        require(state()["connected"]==true && state()["phase"]=="idle","Connect an idle machine first");
        require(!calibrated,"Machine is already prepared; disconnect and repeat the power-cycle procedure");
        for (const char* key : {"power_cycled","pen_on_paper","bottom_confirmed"}) require(data.contains(key) && data[key].is_boolean() && data[key]==true,"Confirm power cycle, paper contact and lowest position first");
        require(starts(status(),"<Idle"),"Machine must be Idle");
        manual_cancel=false; stopped=false; paused=false;
        update({{"phase","preparing"},{"operation_type","manual"},{"error",nullptr},{"last_action","Raising Z by 13.5 mm to establish this session's zero"},{"motion_revision",state()["motion_revision"].get<int>()+1}});
        busy=true;
        try { worker=std::thread(&Controller::prepare_contact,this); } catch (...) { busy=false;update({{"phase","failed"},{"operation_type",nullptr}});throw; }
        return state();
    }
    if (contact_startup && (action=="start" || action=="jog" || action=="pen" || action=="check")) require(calibrated,"Confirm paper contact and prepare the machine first");
    if (action == "start") {
        auto id = data.at("job_id").get<std::string>(); auto folder = job_path(id);
        std::lock_guard<std::mutex> jobs(jobs_mutex); auto meta = manifest(folder);
        if (fs::exists(folder/"consumed")) {
            auto s = state(); require(s["job_id"] == id, "Task already executed; generate a new task"); return s;
        }
        join_finished(); require(state()["connected"] == true, "Connect the machine first");
        require(state()["phase"] == "idle" || state()["phase"] == "complete" || state()["phase"] == "stopped", "Handle the current machine state first");
        require(data.value("origin_confirmed",false), "Confirm paper and origin first");
        require(fs::exists(folder/"ready"), "Task upload is incomplete"); double max_feed = validate_job(folder,meta);
        require(control_config(meta.value("device_control",Json::object()))==device_control,"Task device settings differ; regenerate preview for this machine");
        if (meta.contains("device_port")) require(meta["device_port"]==state()["port"],"Task belongs to another device; regenerate preview");
        auto probe=check(meta); atomic_json(folder/"probe.json",probe);
        // A durable claim precedes any motion. Crashes require a new job ID.
        claim_job(folder);
        stopped = false; stop_requested = false; paused = false;
        write("\x90",true);
        override_pending = -1;
        update({{"feed_override_requested",100},{"feed_override",nullptr}});
        update({{"speed_max_percent",std::min(200,static_cast<int>(std::floor(MaxTaskFeed * 100 / max_feed)))}});
        status();
        update({{"phase","running"},{"operation_type","writing"},{"job_id",id},{"acknowledged",0},{"total",meta.at("commands")},{"error",nullptr},{"task_origin",probe["position"]},{"returned_to_origin",false}});
        try { save(folder); } catch (const std::exception& e) { failure(e.what(),false); throw; }
        busy = true;
        try { worker = std::thread(&Controller::run_job,this,folder,probe["position"]); } catch (...) { busy=false; update({{"phase","failed"},{"operation_type",nullptr}}); throw; }
        return state();
    }
    join_finished(); require(state()["connected"] == true, "Connect the machine first");
    require(state()["phase"] == "idle" || state()["phase"] == "complete" || state()["phase"] == "stopped", "Handle the current machine state first");
    if (action == "check") { check(data.value("metadata",Json::object())); return state(); }
    std::vector<std::string> steps;
    if (action == "jog") {
        auto axis = data.at("axis").get<std::string>(); require(axis=="X" || axis=="Y", "Jog axis must be X or Y");
        double distance=numeric(data,"distance",-20,20), feed=numeric(data,"feed",50,1000); require(std::abs(distance)>=.01,"Jog distance must be at least 0.01 mm");
        std::ostringstream cmd; cmd << "$J=G21 G91 " << axis << distance << " F" << feed; steps.push_back(cmd.str());
    } else if (action == "pen") {
        auto position = data.at("position").get<std::string>(); require(position=="up" || position=="down","Invalid pen position");
        require(starts(status(),"<Idle"),"Machine must be Idle"); auto p=state()["position"]; require(p.is_array(),"Missing pen position");
        if (position=="up" && state()["pen_top"]==true) return state();
        auto up="$J=G21 G90 G53 Z"+decimal(machine_z(device_control["pen_up"]))+" F"+decimal(device_control["pen_feed"]);
        auto down="$J=G21 G90 G53 Z"+decimal(machine_z(device_control["pen_down"]))+" F"+decimal(device_control["pen_feed"]);
        if (position=="down") { if (state()["pen_top"]!=true) steps.push_back(up); steps.push_back(down); }
        steps.push_back(up);
    } else throw std::runtime_error("Unknown control action");
    require(starts(status(),"<Idle"),"Machine must be Idle");
    manual_cancel=false; stopped=false; paused=false; auto s=state();
    update({{"phase","manual"},{"operation_type","manual"},{"motion_revision",s["motion_revision"].get<int>()+1},{"error",nullptr},{"last_action",action}});
    busy=true;
    try { worker=std::thread(&Controller::run_manual,this,steps); } catch (...) { busy=false; update({{"phase","failed"},{"operation_type",nullptr}}); throw; }
    return state();
    } catch (const SerialError& e) { failure(e.what(),false); throw; }
}
void Controller::failure(const std::string& message, bool manual) {
    expected_reset=false;
    if (contact_startup) { calibrated=false;update({{"calibrated",false},{"logical_z",nullptr}}); }
    try { if (serial) write(manual ? "\x85" : "!",true); } catch (...) {}
    update({{"phase","failed"},{"operation_type",nullptr},{"position",nullptr},{"pen_top",nullptr},{"pen","unknown"},{"error",message}});
}
void Controller::save(const fs::path& folder) { atomic_json(folder/"state.json",state()); }
void Controller::run_manual(std::vector<std::string> steps) {
    try {
        for (auto& step : steps) { if (manual_cancel) break; command(step,false,true); wait_idle(false); }
        std::lock_guard<std::mutex> tx(tx_mutex);
        update({{"phase","idle"},{"operation_type",nullptr},{"last_action",manual_cancel ? "Manual movement cancelled" : "Manual movement complete"}});
    } catch (const Cancelled&) {} catch (const std::exception& e) { failure(e.what(),true); }
    busy=false;
}
void Controller::prepare_contact() {
    try {
        auto initial=state()["position"];
        command("$J=G21 G91 Z-13.5 F100",false,true); wait_idle(false);
        require(!manual_cancel,"Preparation cancelled; repeat power-cycle procedure");
        auto p=state()["position"];
        require(initial.is_array() && p.is_array() && std::abs(p[2].get<double>()-initial[2].get<double>()+13.5)<=.02 && std::abs(p[0].get<double>()-initial[0].get<double>())<=.02 && std::abs(p[1].get<double>()-initial[1].get<double>())<=.02,"Unexpected preparation position");
        top_z=p[2];
        // Re-establish a complete work origin; saved firmware offsets may be
        // nonzero. G92 changes coordinates only, never physical position.
        command("G54");command("G92 X0 Y0 Z0");
        auto replies=command("$#");std::map<std::string,std::vector<double>> offsets;
        for (const auto& line:replies) { std::smatch m;if(std::regex_match(line,m,std::regex("\\[(G54|G92|TLO):([0-9.,-]+)\\]")))offsets[m[1]]=values(m[2]); }
        calibrated=true;verify_offsets(offsets);status();
        require(!manual_cancel,"Preparation cancelled; repeat power-cycle procedure");
        update({{"calibrated",true},{"phase","idle"},{"operation_type",nullptr},{"last_action","Prepared: top Z0, paper Z13.5; do not move by hand or adjust the spring"}});
    } catch(const Cancelled&) { calibrated=false;update({{"calibrated",false}}); }
      catch(const std::exception& e) { calibrated=false;failure(e.what(),true); }
    busy=false;
}
void Controller::return_after_stop(const fs::path& folder, const Json& origin) {
    require(origin.is_array() && origin.size()==3 && std::abs(origin[2].get<double>()-machine_z(device_control["pen_up"]))<=.01,"Missing task origin");
    auto deadline=Clock::now()+15s;
    Json held;
    for (;;) {
        auto report=status();
        if (starts(report,"<Hold:0|")) { held=state()["position"]; break; }
        // An Idle report may predate the hold request. Require its explicit
        // completion acknowledgement before resetting a potentially moving axis.
        require(starts(report,"<Hold:1|") || starts(report,"<Run|") || starts(report,"<Idle"),"Machine did not stop safely");
        require(Clock::now()<deadline,"Feed hold completion timeout"); std::this_thread::sleep_for(30ms);
    }
    require(held.is_array() && held.size()==3,"Missing stopped machine position");
    // Reset only after a complete feed hold. Wait for a fresh startup banner so
    // stale queued status/ack replies cannot authorize recovery motion.
    expected_reset=true;write("\x18",true);
    deadline=Clock::now()+8s; bool initialized=false;
    while (Clock::now()<deadline) { if (starts(read(),"Grbl ")) { initialized=true; break; } }
    require(initialized,"Controller reset confirmation missing; return cancelled");
    expected_reset=false;
    if (contact_startup) { command("G54");command("G92 Z"+decimal(held[2].get<double>()-top_z)); }
    auto probe=check(manifest(folder),false);
    for (size_t i=0;i<3;++i) require(std::abs(probe["position"][i].get<double>()-held[i].get<double>())<=.01,"Position changed after reset; return cancelled");
    update({{"phase","returning"},{"last_action","Raising pen before returning to task origin"}});
    command("$J=G21 G90 G53 Z"+decimal(machine_z(device_control["pen_up"]))+" F"+decimal(device_control["pen_feed"])); wait_idle(false);
    require(state()["pen_top"]==true,"Pen did not reach configured up position; XY return cancelled");
    update({{"last_action","Returning XY to task origin"}});
    std::ostringstream target; target.imbue(std::locale::classic());
    target<<std::fixed<<std::setprecision(3)<<"$J=G21 G90 G53 X"<<origin[0].get<double>()<<" Y"<<origin[1].get<double>()<<" F1000";
    command(target.str()); wait_idle(false);
    auto position=state()["position"];
    require(position.is_array() && position.size()==3,"Missing final machine position");
    for (size_t i=0;i<3;++i) require(std::abs(position[i].get<double>()-origin[i].get<double>())<=.01,"Machine did not return to task origin");
    std::lock_guard<std::mutex> tx(tx_mutex); cancelled();
    update({{"phase","stopped"},{"operation_type",nullptr},{"returned_to_origin",true},{"last_action","Task stopped; pen raised and returned to task origin"}});
}
void Controller::run_job(fs::path folder, Json origin) {
    try {
        { std::lock_guard<std::mutex> lock(log_mutex); log.open(folder/"serial.jsonl",std::ios::app); require(bool(log),"Cannot open serial log"); }
        std::ifstream file(folder/"job.gcode"); std::string line; size_t count=0;
        while (std::getline(file,line)) {
            command(line,true); cancelled(); update({{"acknowledged",++count}});
            if (Clock::now() - last_poll > 100ms) { status(); last_poll = Clock::now(); }
            if (count%25==0) save(folder);
        }
        require(file.eof(),"Cannot read task file");
        { std::lock_guard<std::mutex> tx(tx_mutex); cancelled(); if (!paused) update({{"phase","draining"}}); }
        wait_idle(true);
        auto parameters=command("$#"); std::map<std::string,std::vector<double>> offsets;
        for (auto& item : parameters) { std::smatch m; if (std::regex_match(item,m,std::regex("\\[(G54|G92|TLO):([0-9.,-]+)\\]"))) offsets[m[1]]=values(m[2]); }
        auto p=state()["position"];
        require(p.is_array(),"Missing final origin coordinates");verify_offsets(offsets);
        for (size_t i=0;i<3;++i) require(std::abs(p[i].get<double>()-(i==2?machine_z(device_control["pen_up"]):offsets["G54"][i]+offsets["G92"][i]))<=.01,"Machine did not return to the task origin");
        { std::lock_guard<std::mutex> tx(tx_mutex); cancelled(); update({{"phase","complete"},{"operation_type",nullptr}}); }
    } catch (const Cancelled&) {} catch (const std::exception& e) {
        stop_requested=false; failure(e.what(),false);
    }
    if (stop_requested && !stopped) {
        stop_requested=false;
        try { return_after_stop(folder,origin); }
        catch (const Cancelled&) {} catch (const std::exception& e) { failure(e.what(),false); }
    }
    if (stopped) {
        std::lock_guard<std::mutex> tx(tx_mutex); serial.reset();
        if(contact_startup){calibrated=false;update({{"calibrated",false},{"logical_z",nullptr}});}
        update({{"phase","stopped"},{"connected",false},{"operation_type",nullptr},{"position",nullptr},{"pen","unknown"},{"pen_top",nullptr},{"status",""},{"returned_to_origin",false},{"last_action","Return cancelled; reconnect and confirm position"}});
    }
    try { save(folder); } catch (const std::exception& e) { failure(e.what(),false); }
    { std::lock_guard<std::mutex> lock(log_mutex); log.close(); }
    busy=false;
}

fs::path Controller::job_path(const std::string& id) { require(std::regex_match(id,std::regex("[a-f0-9]{32}")),"Invalid task ID"); return root/id; }
Json Controller::manifest(const fs::path& folder) { std::ifstream f(folder/"manifest.json"); require(bool(f),"Task not found"); Json j; f>>j; return j; }
double Controller::validate_job(const fs::path& folder, const Json& meta) {
    require(meta.contains("commands") && meta["commands"].is_number_integer(),"Invalid command count");
    auto total=meta["commands"].get<int>(); require(total>=7 && total<=1000000,"Invalid command count");
    require(meta.contains("sha256") && meta["sha256"].is_string() && std::regex_match(meta["sha256"].get<std::string>(),std::regex("[a-f0-9]{64}")),"Invalid SHA-256");
    auto paper=meta.at("machine_paper"), bounds=meta.at("machine_page_bounds");
    require(paper.is_array() && paper.size()==2 && bounds.is_array() && bounds.size()==4,"Missing machine bounds");
    for (auto& v : paper) require(v.is_number() && std::isfinite(v.get<double>()) && v.get<double>()>0 && v.get<double>()<=600,"Invalid machine dimensions");
    for (auto& v : bounds) require(v.is_number() && std::isfinite(v.get<double>()) && std::abs(v.get<double>())<=600,"Invalid machine bounds");
    for (int i=0;i<2;++i) require(bounds[i].get<double>()<=0 && bounds[i+2].get<double>()>=0 && std::abs(bounds[i+2].get<double>()-bounds[i].get<double>()-paper[i].get<double>())<.002,"Inconsistent machine bounds");
    auto path=folder/"job.gcode"; require(fs::file_size(path)<=32*1024*1024,"Task exceeds 32 MiB");
    std::ifstream raw(path,std::ios::binary); require(bool(raw),"Cannot read task"); picosha2::hash256_one_by_one hash; char chunk[8192];
    while (raw.read(chunk,sizeof(chunk)) || raw.gcount()) hash.process(chunk,chunk+raw.gcount());
    require(raw.eof(),"Cannot read task"); hash.finish(); require(picosha2::get_hash_hex_string(hash)==meta["sha256"],"Task checksum mismatch");
    auto task_control=control_config(meta.value("device_control",Json::object()));
    // Upload validation is independent of the connected machine; start checks the binding.
    auto up_line="G1 Z"+decimal(task_control["pen_up"])+" F3000";
    std::ifstream f(path); std::string line, previous,last; int count=0; double max_feed=3000;
    const std::vector<std::string> header={"G21","G90","G94",up_line,"G92 X0 Y0"};
    const std::regex motion("G1 (?:(X(-?[0-9]+(?:\\.[0-9]+)?) Y(-?[0-9]+(?:\\.[0-9]+)?))|Z(-?[0-9]+(?:\\.[0-9]+)?)) F([0-9]+(?:\\.[0-9]+)?)");
    while (std::getline(f,line)) {
        require(line.size()<78,"Invalid G-code line");
        if (count<5) require(line==header[count],"Invalid task header");
        else {
            std::smatch m; require(std::regex_match(line,m,motion),"Unsupported G-code command");
            double feed=std::stod(m[5]); require(std::isfinite(feed) && feed>=100 && feed<=MaxTaskFeed,"Invalid task feed");
            max_feed=std::max(max_feed,feed);
            if (m[4].matched) {
                double z=std::stod(m[4]);
                require(std::abs(z-task_control["pen_up"].get<double>())<.00001 || std::abs(z-task_control["pen_down"].get<double>())<.00001,"Z does not match device profile");
            }
            if (m[1].matched) for (int axis=0;axis<2;++axis) {
                double value=std::stod(m[axis+2]); require(value>=bounds[axis].get<double>()-.001 && value<=bounds[axis+2].get<double>()+.001,"Motion exceeds task bounds");
            }
        }
        previous=last; last=line; ++count;
    }
    require(count==total && previous==up_line && last=="G1 X0 Y0 F3000","Invalid task trailer or line count");
    return max_feed;
}
Json Controller::upload(const std::string& id, const std::string& action, const Json& data) {
    require(data.is_object(),"Expected JSON object"); std::lock_guard<std::mutex> lock(jobs_mutex); auto folder=job_path(id);
    if (action=="begin") {
        if (fs::exists(folder/"manifest.json")) require(manifest(folder)==data,"Task ID already contains different content");
        else { fs::create_directories(folder); atomic_json(folder/"manifest.json",data); std::ofstream f(folder/"job.gcode",std::ios::binary); require(bool(f),"Cannot create task file"); }
    } else {
        manifest(folder); require(!fs::exists(folder/"consumed"),"Task already executed");
        if (action=="chunk") {
            require(!fs::exists(folder/"ready"),"Task is already sealed");
            require(data.at("offset").is_number_unsigned() || data.at("offset").is_number_integer(),"Invalid upload offset");
            auto offset=data.at("offset").get<int64_t>(); auto content=data.at("content").get<std::string>();
            require(offset>=0 && content.size()<=32768 && offset+content.size()<=32*1024*1024,"Invalid upload size");
            for (unsigned char c : content) require(c=='\n' || (c>=32 && c<127),"Task must contain ASCII G-code");
            auto size=fs::file_size(folder/"job.gcode");
            if (static_cast<uint64_t>(offset)<size) {
                require(offset+content.size()<=size,"Overlapping task chunk");
                std::ifstream f(folder/"job.gcode",std::ios::binary); f.seekg(offset); std::string existing(content.size(),'\0'); f.read(existing.data(),existing.size());
                require(existing==content,"Conflicting task chunk");
            } else {
                require(static_cast<uint64_t>(offset)==size,"Unexpected upload offset");
                std::ofstream f(folder/"job.gcode",std::ios::binary|std::ios::app); f<<content; f.flush(); require(bool(f),"Cannot write task chunk");
            }
        } else if (action=="seal") {
            validate_job(folder,manifest(folder)); std::ofstream ready(folder/"ready"); ready<<"validated\n"; ready.flush(); require(bool(ready),"Cannot seal task");
        } else throw std::runtime_error("Unknown upload action");
    }
    return {{"job_id",id},{"offset",fs::file_size(folder/"job.gcode")},{"ready",fs::exists(folder/"ready")},{"consumed",fs::exists(folder/"consumed")}};
}
}
